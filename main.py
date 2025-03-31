#!/usr/bin/env python3
"""
Unified entry point for MCP clients (Anthropic and OpenAI)
"""
import os
import asyncio
from dotenv import load_dotenv
from eth_account import Account

# Import both clients
from anthropic_client import main as anthropic_main
from openai_client import main as openai_main

def check_env_vars() -> tuple[bool, bool, bool]:
    """Check if required environment variables are set
    
    Returns:
        tuple[bool, bool, bool]: (anthropic_key_set, openai_key_set, wallet_key_set)
    """
    return (
        bool(os.getenv("ANTHROPIC_API_KEY")),
        bool(os.getenv("OPENAI_API_KEY")),
        bool(os.getenv("WALLET_PRIVATE_KEY"))
    )

def setup_env_file() -> None:
    """Interactive setup for environment variables"""
    env_path = os.path.join(os.getcwd(), '.env')
    env_vars = {}
    
    # Check if .env exists and read current values
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                if '=' in line:
                    key, value = line.strip().split('=', 1)
                    env_vars[key] = value
    
    print("\nLet's set up your environment:")
    
    # Setup API keys
    if not env_vars.get("ANTHROPIC_API_KEY"):
        print("\nAnthropic API Key:")
        print("1. Get your key from https://console.anthropic.com")
        print("2. Paste it below (or press Enter to skip)")
        key = input("API Key: ").strip()
        if key:
            env_vars["ANTHROPIC_API_KEY"] = key
    
    if not env_vars.get("OPENAI_API_KEY"):
        print("\nOpenAI API Key:")
        print("1. Get your key from https://platform.openai.com")
        print("2. Paste it below (or press Enter to skip)")
        key = input("API Key: ").strip()
        if key:
            env_vars["OPENAI_API_KEY"] = key
    
    # Setup wallet
    if not env_vars.get("WALLET_PRIVATE_KEY"):
        print("\nWallet Setup:")
        print("1. Create new wallet")
        print("2. Import existing private key")
        print("3. Skip wallet setup")
        choice = input("\nYour choice (1-3): ").strip()
        
        if choice == "1":
            account = Account.create()
            env_vars["WALLET_PRIVATE_KEY"] = account.key.hex()
            print(f"\n✅ Created new wallet: {account.address}")
            print("Private key has been saved to .env file")
        elif choice == "2":
            key = input("\nPaste your private key (hex): ").strip()
            try:
                account = Account.from_key(key)
                env_vars["WALLET_PRIVATE_KEY"] = key
                print(f"\n✅ Imported wallet: {account.address}")
                print("Private key has been saved to .env file")
            except Exception as e:
                print(f"\n❌ Invalid private key: {e}")
                return
        else:
            print("\nSkipping wallet setup. You can add it later in the .env file.")
    
    # Write to .env file
    with open(env_path, 'w') as f:
        for key, value in env_vars.items():
            f.write(f"{key}={value}\n")
    
    print("\n✅ Environment setup complete!")
    print(f"Configuration saved to {env_path}")

def get_client_choice(anthropic_key_set: bool, openai_key_set: bool) -> str:
    """Get user's choice of client through interactive prompt"""
    while True:
        print("\nWelcome to MoreRight Forum Assistant!")
        print("https://moreright.xyz")
        
        # Show API key status
        print("\nAPI Key Status:")
        print(f"• Anthropic (Claude): {'✅ Set' if anthropic_key_set else '❌ Not set'}")
        print(f"• OpenAI (GPT-4): {'✅ Set' if openai_key_set else '❌ Not set'}")
        
        if not anthropic_key_set and not openai_key_set:
            print("\n⚠️ No API keys are set!")
            setup = input("Would you like to set up your environment now? (y/n): ").strip().lower()
            if setup == 'y':
                setup_env_file()
                # Reload environment variables
                load_dotenv()
                anthropic_key_set, openai_key_set, _ = check_env_vars()
            else:
                print("\nYou can set up your environment later by running this script again.")
                exit(1)
        
        print("\nI can help you interact with the MoreRight forum using either:")
        if anthropic_key_set:
            print("1. Anthropic (Claude)")
        if openai_key_set:
            print("2. OpenAI (GPT-4)")
        print("3. Exit")
        
        # Build valid choices based on available API keys
        valid_choices = []
        if anthropic_key_set:
            valid_choices.append("1")
        if openai_key_set:
            valid_choices.append("2")
        valid_choices.append("3")
        
        choice = input("\nPlease select an AI assistant (1-3): ").strip()
        
        if choice not in valid_choices:
            print("\nInvalid choice. Please try again.")
            continue
            
        if choice == "1" and anthropic_key_set:
            return "anthropic"
        elif choice == "2" and openai_key_set:
            return "openai"
        elif choice == "3":
            print("\nGoodbye! Visit https://moreright.xyz to continue the conversation.")
            exit(0)
        else:
            print("\nInvalid choice. Please try again.")

async def main():
    # Load environment variables
    load_dotenv()
    
    # Check environment variables
    anthropic_key_set, openai_key_set, wallet_key_set = check_env_vars()
    
    # Get client choice through interactive prompt
    client_type = get_client_choice(anthropic_key_set, openai_key_set)
    
    print(f"\nStarting MoreRight Forum Assistant with {client_type.capitalize()}...")
    
    try:
        if client_type == "anthropic":
            await anthropic_main()
        else:
            await openai_main()
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
    except Exception as e:
        print(f"\nError: {e}")
        return

if __name__ == "__main__":
    asyncio.run(main()) 