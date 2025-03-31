import json
import asyncio
import os
from dotenv import load_dotenv
from typing import List, Dict, Optional, Tuple, Union
from anthropic import AsyncAnthropic, APIError
from mcp import types, ClientSession
from eth_account import Account
from eth_account.messages import encode_defunct
from mcp.client.sse import sse_client

# ------------------------------------------------------------------------------------
# Constants
# ------------------------------------------------------------------------------------
DEFAULT_CLAUDE_MODEL = "claude-3-5-sonnet-20241022"
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", DEFAULT_CLAUDE_MODEL)
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "4096"))

# ------------------------------------------------------------------------------------
# Transforming Tools
# ------------------------------------------------------------------------------------

def mcp_tools_to_claude_tool_list(mcp_tool_list: List[types.Tool]) -> List[Dict]:
    """
    Convert each MCP tool into the format expected by Claude (Anthropic).
    Particularly important for aligning input schemas with Claude.
    """
    claude_tools = []
    for tool in mcp_tool_list:
        # Provide default schemas if none are present on the server side.
        if tool.name in {"get_wallet_challenge", "verify_wallet_signature"}:
            tool_entry = {
                "name": tool.name,
                "description": tool.description or (
                    "This tool is part of the authentication flow. Use it to verify "
                    "ownership of a wallet address by signing a challenge message. "
                    "It does not require a verified wallet session."
                ),
                "input_schema": tool.inputSchema or {
                    "type": "object",
                    "properties": {
                        "address": {
                            "type": "string",
                            "description": "Ethereum wallet address"
                        }
                    },
                    "required": ["address"]
                }
            }
        else:
            # Tools that require auth, typically we handle `auth_token` automatically
            tool_entry = {
                "name": tool.name,
                "description": tool.description or (
                    "This tool requires a verified wallet session (handled via auth_token). "
                    "Use this when the user requests an action that requires authentication."
                ),
                "input_schema": tool.inputSchema or {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }
        claude_tools.append(tool_entry)
    return claude_tools

# ------------------------------------------------------------------------------------
# System Prompt Construction
# ------------------------------------------------------------------------------------

def build_claude_system_prompt(
    claude_tools: List[Dict],
    auth_token: Optional[str],
    wallet_address: Optional[str] = None
) -> str:
    """Build minimal system prompt with essential instructions."""
    tool_summary = "\n".join(
        f"- {tool['name']}: {tool['description'].split('.')[0]}"
        for tool in claude_tools
    )
    
    user_status = "not verified"
    if auth_token:
        user_status = "verified"

    return f"""You are an AI assistant for the moreright forum. User is {user_status}.

CRITICAL RULES:
1. ALWAYS use tool_use blocks for actions
2. For multi-step tasks, you MUST complete all steps:
   - If search then react, you must do both
   - Never stop after the first action
   - Chain tool calls in a single response
3. Never expose auth tokens
4. Be concise
5. When a user asks about "my profile", "my posts", "my wallet", or "my anything", use their authenticated wallet address ({wallet_address}) as their user ID


Tools:
{tool_summary}

Example of chaining:
User: "Search for X and react to top post"
You must:
1. Call search_forum
2. Get result and immediately call add_reaction
3. Both tools in same response""".strip()

# ------------------------------------------------------------------------------------
# Helper Functions
# ------------------------------------------------------------------------------------

async def extract_text_content(result) -> str:
    """
    Helper to safely extract the text portion of a tool response.
    Returns "[No text returned]" if none is found.
    """
    if hasattr(result, 'content') and result.content:
        for content_item in result.content:
            if content_item.type == 'text':
                return content_item.text
    return "[No text returned by tool]"

# ------------------------------------------------------------------------------------
# Tool Use Handler
# ------------------------------------------------------------------------------------

def prune_conversation(messages: List[Dict], max_messages: int = 10) -> List[Dict]:
    """
    Prune the conversation history to keep token count low.
    Keeps the most recent messages and summarizes older ones.
    """
    if len(messages) <= max_messages:
        return messages
        
    # Keep the most recent messages
    recent_messages = messages[-max_messages:]
    
    # For older messages, create a summary
    older_messages = messages[:-max_messages]
    
    # Extract text content from older messages
    older_content = []
    for msg in older_messages:
        if msg["role"] == "user":
            # For user messages, just get the text
            for content in msg["content"]:
                if content["type"] == "text":
                    older_content.append(f"User asked: {content['text']}")
        elif msg["role"] == "assistant":
            # For assistant messages, note if tools were used
            has_tools = any(c["type"] == "tool_use" for c in msg["content"])
            if has_tools:
                older_content.append("Assistant used tools to fulfill request")
            else:
                # Get text response if any
                texts = [c["text"] for c in msg["content"] if c["type"] == "text"]
                if texts:
                    older_content.append(f"Assistant responded: {texts[0][:100]}...")

    # Create a summary message
    if older_content:
        summary_message = {
            "role": "system",
            "content": [{"type": "text", "text": "Previous conversation summary:\n" + "\n".join(older_content[-5:])}]
        }
        return [summary_message] + recent_messages
    
    return recent_messages

async def handle_tool_use_response(
    client: AsyncAnthropic,
    messages: List[Dict],
    assistant_message: Dict,
    session: ClientSession,
    model_name: str,
    auth_token: Optional[str] = None,
    tools: Optional[List[Dict]] = None,
    debug: bool = False
) -> List[Dict]:
    """Handle tool use responses with minimal context."""
    # Keep only last 10 messages plus current exchange
    if len(messages) > 10:
        messages = messages[-10:]
    
    tool_use_blocks = [b for b in assistant_message["content"] if b.type == "tool_use"]
    if not tool_use_blocks:
        messages.append(assistant_message)
        return messages

    # Execute all tools in sequence
    all_results = []
    for block in tool_use_blocks:
        tool_name = block.name
        tool_input = block.input
        tool_use_id = block.id

        # Add auth token if needed
        if auth_token and tool_name not in {"get_wallet_challenge", "verify_wallet_signature"}:
            tool_input["auth_token"] = auth_token

        try:
            result = await session.call_tool(tool_name, tool_input)
            result_text = await extract_text_content(result)
            
            # Keep tool results concise
            if len(result_text) > 500:
                result_text = result_text[:500] + "..."
                
            tool_result = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result_text
            }
            if debug:
                print(f"\n[Tool {tool_name}] -> {result_text}")
            all_results.append(tool_result)
        except Exception as e:
            tool_result = {
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": f"Tool error: {str(e)}",
                "is_error": True
            }
            print(f"\n[Tool {tool_name}] Error: {str(e)}")
            all_results.append(tool_result)

    # Add the tool use and results as a single exchange
    messages.append(assistant_message)
    messages.append({
        "role": "user",
        "content": all_results
    })

    # Get final response
    try:
        final_response = await client.messages.create(
            model=model_name,
            max_tokens=MAX_TOKENS,
            messages=messages,
            tools=tools,
            system="IMPORTANT: Complete all steps in multi-step tasks. If you just executed a tool, check its result and take any necessary follow-up actions before stopping."
        )
        
        final_assistant_msg = {
            "role": "assistant",
            "content": final_response.content
        }
        
        # If there are more tool calls, handle them
        if final_response.stop_reason == "tool_use":
            return await handle_tool_use_response(
                client=client,
                messages=messages,
                assistant_message=final_assistant_msg,
                session=session,
                model_name=model_name,
                auth_token=auth_token,
                tools=tools,
                debug=debug
            )
        
        # No more tool calls, add final message and return
        messages.append(final_assistant_msg)
        final_text = "\n".join(
            block.text for block in final_response.content if block.type == "text"
        )
        if final_text.strip():
            print(f"\nAssistant: {final_text}")
            
    except APIError as e:
        print(f"\nError communicating with Claude: {e}")
        
    return messages

# ------------------------------------------------------------------------------------
# Wallet Setup & Verification
# ------------------------------------------------------------------------------------

async def create_new_wallet() -> Tuple[str, str]:
    """
    Convenience function that uses eth_account to create a new ephemeral wallet
    and returns (address, private_key).
    """
    account = Account.create()
    print(f"\n✅ Created new wallet: {account.address}")
    return account.address, account.key.hex()

def save_wallet_to_env(private_key: str) -> bool:
    """
    Save wallet private key to .env file for future use.
    
    Args:
        private_key: The private key to save
        
    Returns:
        True if saved successfully, False otherwise
    """
    try:
        env_path = os.path.join(os.getcwd(), '.env')
        
        # Check if .env exists and read its content
        if os.path.exists(env_path):
            with open(env_path, 'r') as env_file:
                lines = env_file.readlines()
                
            # Check if WALLET_PRIVATE_KEY already exists
            key_exists = False
            for i, line in enumerate(lines):
                if line.startswith('WALLET_PRIVATE_KEY='):
                    lines[i] = f'WALLET_PRIVATE_KEY={private_key}\n'
                    key_exists = True
                    break
                    
            # If key doesn't exist, append it
            if not key_exists:
                lines.append(f'WALLET_PRIVATE_KEY={private_key}\n')
                
            # Write updated content back to file
            with open(env_path, 'w') as env_file:
                env_file.writelines(lines)
        else:
            # Create new .env file if it doesn't exist
            with open(env_path, 'w') as env_file:
                env_file.write(f'WALLET_PRIVATE_KEY={private_key}\n')
                
        return True
    except Exception as e:
        print(f"Failed to save wallet to .env: {e}")
        return False

async def verify_wallet_with_signature(session: ClientSession, wallet_address: str, private_key: str) -> Optional[str]:
    """
    Attempts to verify wallet ownership by:
    1) Calling get_wallet_challenge(address)
    2) Signing the returned challenge with the private key
    3) Submitting verify_wallet_signature(address, signature)

    Returns an auth_token on success or None on failure.
    """
    try:
        print("\n🔐 Generating challenge message...")
        challenge_response = await session.call_tool("get_wallet_challenge", {"address": wallet_address})

        # Because get_wallet_challenge returns text content with JSON in it
        challenge_text = challenge_response.content[0].text
        challenge_data = json.loads(challenge_text)
        # The challenge message is often stored in challenge_data["content"][0]["text"]
        # but do some checks to avoid KeyErrors
        challenge_items = challenge_data.get("content", [])
        if not challenge_items:
            print("❌ Challenge message was empty.")
            return None
        challenge_msg = challenge_items[0].get("text", "")

        if not challenge_msg:
            print("❌ Challenge message was empty.")
            return None

        # Sign the challenge using eth_account
        message = encode_defunct(text=challenge_msg)
        signed_message = Account.sign_message(message, private_key=private_key)
        signature = signed_message.signature.hex()

        print("📝 Sending signed challenge...")
        verification_response = await session.call_tool(
            "verify_wallet_signature",
            {"address": wallet_address, "signature": signature}
        )

        # The response typically has an auth_token
        verification_text = verification_response.content[0].text
        verification_data = json.loads(verification_text)
        token = verification_data.get("auth_token")

        if not token:
            print("❌ Wallet verification failed. No token received.")
        return token

    except Exception as e:
        print(f"Wallet verification failed: {e}")
        return None

async def setup_wallet(session: ClientSession, client: AsyncAnthropic) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Interactive flow to either:
    1. Create a new ephemeral wallet
    2. Import an existing private key
    3. Enter just an address (read-only, no verification)
    4. Skip the entire process
    """
    print("\n🛡️ Wallet setup\nChoose one:")
    print("1. Create new wallet (ephemeral)")
    print("2. Import private key")
    print("3. Enter wallet address only (read-only)")
    print("4. Skip")

    choice = input("\nYour choice: ").strip()

    if choice == "1":
        address, private_key = await create_new_wallet()
        token = await verify_wallet_with_signature(session, address, private_key)
        
        # Ask if they want to save the private key
        if token is not None:
            save_choice = input("\nSave this wallet to .env file for future use? (y/n): ").lower().strip()
            if save_choice == 'y' or save_choice == 'yes':
                if save_wallet_to_env(private_key):
                    print("✅ Wallet saved to .env file.")
                    print(f"Address: {address}")
                    print("Next time, it will be loaded automatically.")
                else:
                    print("❌ Failed to save wallet. Please note your private key:")
                    print(f"Private key: {private_key}")
        else:
            print("\n⚠️ Wallet verification failed. You can restart and try again.")
            
        return address, private_key, token

    elif choice == "2":
        pk = input("Paste private key (hex): ").strip()
        account = Account.from_key(pk)
        print(f"\n🔓 Loaded wallet: {account.address}")
        token = await verify_wallet_with_signature(session, account.address, pk)
        
        # Ask if they want to save the private key
        if token is not None:
            save_choice = input("\nSave this private key to .env file for future use? (y/n): ").lower().strip()
            if save_choice == 'y' or save_choice == 'yes':
                if save_wallet_to_env(pk):
                    print("✅ Private key saved to .env file.")
                    print(f"Address: {account.address}")
                    print("Next time, it will be loaded automatically.")
                else:
                    print("❌ Failed to save private key.")
        else:
            print("\n⚠️ Wallet verification failed. You can restart and try again.")
            
        return account.address, pk, token

    elif choice == "3":
        address = input("Enter wallet address (0x...): ").strip()
        print(f"\n👀 Using read-only wallet: {address}")
        return address, None, None

    print("\n➡️ Skipping wallet setup.")
    return None, None, None

# ------------------------------------------------------------------------------------
# Main Chat Loop
# ------------------------------------------------------------------------------------

async def hybrid_chat(
    session: ClientSession,
    wallet_address: Optional[str],
    client: AsyncAnthropic,
    model_name: str,
    auth_token: Optional[str] = None,
    debug: bool = False
):
    """Simple chat loop with minimal context."""
    messages = []  # Maintain conversation history
    print("\nAssistant: Hi! How can I help with the moreright forum?")

    while True:
        user_input = input("\nYou: ").strip()
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            print("\nAssistant: Goodbye!")
            break
        
        # Keep only last 15 messages for context
        if len(messages) > 15:
            messages = messages[-15:]

        user_message = {
            "role": "user",
            "content": [{"type": "text", "text": user_input}]
        }

        try:
            tools_response = await session.list_tools()
            claude_tools = mcp_tools_to_claude_tool_list(tools_response.tools)
            system_prompt = build_claude_system_prompt(
                claude_tools=claude_tools,
                auth_token=auth_token,
                wallet_address=wallet_address
            )

            response = await client.messages.create(
                model=model_name,
                max_tokens=MAX_TOKENS,
                messages=messages + [user_message],
                tools=claude_tools,
                system=system_prompt
            )
            
            assistant_message = {
                "role": "assistant",
                "content": response.content
            }
            
            if response.stop_reason == "tool_use":
                messages = await handle_tool_use_response(
                    client=client,
                    messages=messages + [user_message],
                    assistant_message=assistant_message,
                    session=session,
                    model_name=model_name,
                    auth_token=auth_token,
                    tools=claude_tools,
                    debug=debug
                )
            else:
                messages.append(user_message)
                messages.append(assistant_message)
                final_text = "\n".join(
                    block.text for block in response.content if block.type == "text"
                )
                print(f"\nAssistant: {final_text}")

        except APIError as e:
            print(f"\nError communicating with Claude: {e}")
            continue

# ------------------------------------------------------------------------------------
# Entry Point
# ------------------------------------------------------------------------------------

# At the beginning of your main() function
async def main():
    # Load environment variables
    load_dotenv()
    
    # Create an async Anthropic client
    client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    
    # Get wallet private key from environment
    private_key = os.getenv("WALLET_PRIVATE_KEY")
    
    # Debug mode from environment
    debug = os.getenv("DEBUG", "").lower() in ("true", "1", "yes")
    
    # Connect to the SSE-based MCP server
    mcp_server_url = os.getenv("MCP_SERVER_URL", "https://mcp.moreright.xyz/sse")
    async with sse_client(mcp_server_url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            # Initiate the session with the MCP server
            await session.initialize()
            
            # Skip the interactive wallet setup if private key is provided
            wallet_address = None
            token = None
            
            if private_key:
                # Create account from private key
                account = Account.from_key(private_key)
                wallet_address = account.address
                print(f"\n🔐 Using saved wallet: {wallet_address}")
                
                # Verify the wallet automatically
                print("Verifying wallet...")
                token = await verify_wallet_with_signature(session, wallet_address, private_key)
                if token is None:
                    print("\n⚠️ Wallet verification failed. Check your private key.")
                    print("Continuing without wallet authentication.")
                else:
                    print("✅ Wallet verified successfully!")
            else:
                # Fall back to the interactive setup if no private key is provided
                wallet_address, private_key, token = await setup_wallet(session, client)
            
            # Begin the chat
            await hybrid_chat(
                session=session,
                wallet_address=wallet_address,
                client=client,
                model_name=CLAUDE_MODEL,
                auth_token=token,
                debug=debug
            )

if __name__ == "__main__":
    asyncio.run(main())
