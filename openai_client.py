#!/usr/bin/env python
"""
MCP + OpenAI Agents client using the Agents SDK
"""

import os
import json
import asyncio
import logging
from typing import List, Dict, Optional, Tuple, Any
from dataclasses import dataclass

from dotenv import load_dotenv
from eth_account import Account
from eth_account.messages import encode_defunct

# MCP imports
from mcp.client.sse import sse_client
from mcp import types, ClientSession
from agents.mcp.util import MCPUtil

# OpenAI Agents SDK imports
from agents import (
    Agent,
    Runner,
    RunConfig,
    ModelSettings,
    RunContextWrapper,
    FunctionTool
)

# Setup logging
logging.basicConfig(level=logging.WARNING)  # Change to WARNING level for production
logger = logging.getLogger(__name__)

# Set client logging to WARNING for production
logging.getLogger("mcp.client").setLevel(logging.WARNING)
logging.getLogger("mcp.client.sse").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Environment & Constants
# ---------------------------------------------------------------------------

load_dotenv()
DEFAULT_MCP_SERVER_URL = os.getenv("MCP_SERVER_URL", "https://mcp.moreright.xyz/sse")
DEFAULT_OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4-turbo")
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "4096"))

# ---------------------------------------------------------------------------
# Context & State
# ---------------------------------------------------------------------------

@dataclass
class UserContext:
    """Context for the user's session"""
    wallet_address: Optional[str] = None
    auth_token: Optional[str] = None
    session: Optional[ClientSession] = None

# ---------------------------------------------------------------------------
# Wallet Tools
# ---------------------------------------------------------------------------

def create_wallet() -> Tuple[str, str]:
    """Create a new ephemeral wallet.
    
    Returns:
        Tuple of (address, private_key)
    """
    account = Account.create()
    return account.address, account.key.hex()

def save_wallet_to_env(private_key: str) -> bool:
    """Save wallet private key to .env file.
    
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
        logger.error(f"Failed to save wallet to .env: {e}")
        return False

async def verify_wallet(
    session: ClientSession,
    wallet_address: str,
    private_key: str
) -> Optional[str]:
    """Verify wallet ownership and get auth token.
    
    Args:
        session: The MCP client session
        wallet_address: The Ethereum address to verify
        private_key: The private key to sign with
        
    Returns:
        Auth token if successful, None otherwise
    """
    try:
        # Get challenge message
        challenge_resp = await session.call_tool("get_wallet_challenge", {"address": wallet_address})
        if not challenge_resp or not challenge_resp.content:
            return None
        
        # Parse challenge
        challenge_text = challenge_resp.content[0].text
        challenge_data = json.loads(challenge_text)
        challenge_items = challenge_data.get("content", [])
        if not challenge_items:
            return None
        challenge_msg = challenge_items[0].get("text", "")
        if not challenge_msg:
            return None

        # Sign challenge
        message = encode_defunct(text=challenge_msg)
        signed_message = Account.sign_message(message, private_key=private_key)
        signature = signed_message.signature.hex()

        # Verify signature
        verify_resp = await session.call_tool(
            "verify_wallet_signature",
            {"address": wallet_address, "signature": signature}
        )
        if not verify_resp or not verify_resp.content:
            return None
        
        verify_text = verify_resp.content[0].text
        verify_data = json.loads(verify_text)
        return verify_data.get("auth_token")

    except Exception as e:
        print(f"Wallet verification failed: {e}")
        return None

# ---------------------------------------------------------------------------
# Agent Instructions
# ---------------------------------------------------------------------------

AGENT_INSTRUCTIONS = """\
You are an AI assistant for the moreright forum.

AUTHENTICATION STATUS: The user's wallet has already been verified during startup. The auth_token is automatically injected for all authenticated tools that need it.

FORUM INTERACTION:
1. You're on a message board - always search before answering questions about posts or topics
2. Remember they cant click link's or read MD so format your responses accordingly
3. Include relevant IDs (topic_id, post_id) when referencing content
4. After each response, explain available follow-up actions to help guide the user

FUNCTION CALLING BEST PRACTICES:
1. Call functions immediately when information retrieval is needed - don't guess content
2. When multiple steps are required, complete them all before providing a final response
3. For multi-step tasks (search → view → act), chain function calls appropriately
4. Always process function results before moving to the next step or providing final output
5. Prioritize specific, specialized functions over generic ones when available

CONVERSATION HANDLING:
1. Maintain necessary context across multiple turns of conversation
2. When the user makes a follow-up question about previous content, reference relevant IDs
3. If asked to search and then perform an action, complete both actions in one turn
4. Anticipate and suggest logical next steps based on the current conversation flow

CRITICAL RULES:
1. ALWAYS use functions for actions that require real data or changes
2. Never ask for wallet verification - this has already been handled
3. Never expose auth tokens or private keys to the user
4. Be concise but thorough - don't leave tasks incomplete
5. When a user asks about "my profile", "my posts", "my wallet", or "my anything", use their authenticated wallet address ({wallet_address}) as their user ID
"""

def convert_mcp_tool_to_function_tool(tool: types.Tool, session: ClientSession) -> FunctionTool:
    """Convert MCP tool to function tool with proper auth token handling"""
    
    # Define the tool function
    async def on_invoke_tool(ctx: RunContextWrapper[UserContext], input_json: str) -> str:
        try:
            # Parse the input JSON into kwargs dictionary
            kwargs = json.loads(input_json) if input_json else {}
            
            # For authenticated tools, add auth token
            if tool.name not in {"get_wallet_challenge", "verify_wallet_signature"}:
                kwargs["auth_token"] = ctx.context.auth_token
                
            # Call the MCP tool
            result = await ctx.context.session.call_tool(tool.name, kwargs)
            
            # Format the result
            if len(result.content) == 1:
                return result.content[0].model_dump_json()
            elif len(result.content) > 1:
                return json.dumps([item.model_dump() for item in result.content])
            else:
                return "Error running tool."
        except Exception as e:
            logger.error(f"Error invoking tool {tool.name}: {e}")
            return f"Error running tool: {str(e)}"
    
    # Create and return a FunctionTool directly
    return FunctionTool(
        name=tool.name,
        description=tool.description or "",
        params_json_schema=tool.inputSchema or {},
        on_invoke_tool=on_invoke_tool,
        strict_json_schema=False
    )

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    # 1) Load environment variables
    env_private_key = os.getenv("WALLET_PRIVATE_KEY")
    mcp_server_url = os.getenv("MCP_SERVER_URL", DEFAULT_MCP_SERVER_URL)

    # 2) Create user context
    context = UserContext()

    # 3) Connect to MCP server
    async with sse_client(mcp_server_url) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            # Initialize the session
            await session.initialize()
            
            # Store session in context
            context.session = session

            try:
                # 4) Setup wallet if needed
                if env_private_key:
                    # Use environment private key
                    acct = Account.from_key(env_private_key)
                    context.wallet_address = acct.address
                    print(f"\n🔐 Using saved wallet: {context.wallet_address}")
                    
                    # Verify the wallet automatically
                    print("Verifying wallet...")
                    context.auth_token = await verify_wallet(session, acct.address, env_private_key)
                    
                    if context.auth_token:
                        print("✅ Wallet verified successfully!")
                    else:
                        print("\n⚠️ Wallet verification failed. Check your private key.")
                        print("Continuing without wallet authentication.")
                else:
                    # Interactive wallet setup
                    print("\n🛡️ Wallet setup\nChoose one:")
                    print("1. Create new wallet (ephemeral)")
                    print("2. Import private key")
                    print("3. Enter wallet address only (read-only)")
                    print("4. Skip")

                    choice = input("\nYour choice: ").strip()

                    if choice == "1":
                        # Create wallet directly since it's a local function
                        address, privkey = create_wallet()
                        context.wallet_address = address
                        context.auth_token = await verify_wallet(session, address, privkey)
                        
                        # Ask if they want to save the private key
                        if context.auth_token:
                            save_choice = input("\nSave this wallet to .env file for future use? (y/n): ").lower().strip()
                            if save_choice == 'y' or save_choice == 'yes':
                                if save_wallet_to_env(privkey):
                                    print("✅ Wallet saved to .env file.")
                                    print(f"Address: {address}")
                                    print("Next time, it will be loaded automatically.")
                                else:
                                    print("❌ Failed to save wallet. Please note your private key:")
                                    print(f"Private key: {privkey}")
                    elif choice == "2":
                        pk = input("Paste private key (hex): ").strip()
                        acct = Account.from_key(pk)
                        context.wallet_address = acct.address
                        context.auth_token = await verify_wallet(session, acct.address, pk)
                        
                        # Ask if they want to save the private key
                        if context.auth_token:
                            save_choice = input("\nSave this private key to .env file for future use? (y/n): ").lower().strip()
                            if save_choice == 'y' or save_choice == 'yes':
                                if save_wallet_to_env(pk):
                                    print("✅ Private key saved to .env file.")
                                    print(f"Address: {acct.address}")
                                    print("Next time, it will be loaded automatically.")
                                else:
                                    print("❌ Failed to save private key.")
                    elif choice == "3":
                        context.wallet_address = input("Enter wallet address (0x...): ").strip()
                    else:
                        print("\n➡️ Skipping wallet setup.")

                # 5) Get tools from server
                try:
                    # First get tools from server
                    tools_response = await session.list_tools()
                    if not hasattr(tools_response, 'tools'):
                        raise ValueError("No tools found in server response")
                    
                    # Convert MCP tools to function tools
                    mcp_tools = []
                    for tool in tools_response.tools:
                        function_tool = convert_mcp_tool_to_function_tool(tool, session)
                        mcp_tools.append(function_tool)
                    
                except Exception as e:
                    logger.error(f"Failed to get tools from server: {e}")
                    raise

                # 6) Create agent with server tools
                try:
                    # Configure the agent without explicit API settings
                    formatted_instructions = AGENT_INSTRUCTIONS.format(wallet_address=context.wallet_address)
                    agent = Agent[UserContext](
                        name="MorerightAssistant",
                        instructions=formatted_instructions,
                        tools=mcp_tools,
                        model=DEFAULT_OPENAI_MODEL # Use model from environment or default
                    )
                except Exception as e:
                    logger.error(f"Failed to create agent: {e}")
                    raise

                # 7) Enter chat loop
                print("\nAssistant: Hi! How can I help with the moreright forum?")

                while True:
                    user_input = input("\nYou: ").strip()
                    if not user_input:
                        continue
                    if user_input.lower() in {"exit", "quit"}:
                        print("Assistant: Goodbye!")
                        break

                    try:
                        # Run with max_tokens configuration from environment
                        result = await Runner.run(
                            agent, 
                            user_input, 
                            context=context,
                            run_config=RunConfig(
                                model_settings=ModelSettings(
                                    max_tokens=MAX_TOKENS
                                )
                            )
                        )
                        print(f"Assistant: {result.final_output}")
                    except Exception as e:
                        logger.error(f"Error in chat loop: {e}")
                        print(f"\nError: {e}")

            finally:
                # Clean up - nothing to do since session is handled by context manager
                pass

if __name__ == "__main__":
    asyncio.run(main())
