# MoreRight Forum Assistant

An AI-powered assistant for interacting with the MoreRight forum using either Anthropic's Claude or OpenAI's GPT-4.

## Features

- **Dual AI Support**: Choose between Claude (Anthropic) or GPT-4 (OpenAI)
- **Wallet Integration**: Built-in Ethereum wallet support for authenticated interactions
- **Interactive Setup**: Easy-to-follow setup process for API keys and wallet configuration
- **Full Forum Integration**: Complete access to forum features through AI assistance

## Prerequisites

- Python 3.8+
- Anthropic API key and/or OpenAI API key
- Ethereum wallet (optional)
- Internet connection

## Installation

1. Clone the repository:
```bash
git clone https://github.com/yourusername/moreright-forum-assistant.git
cd moreright-forum-assistant
```

2. Create and activate a virtual environment (recommended):
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Configure your environment:
```bash
cp .env.example .env
```

5. Edit `.env` with your settings:
- Add your API keys (ANTHROPIC_API_KEY and/or OPENAI_API_KEY)
- (Optional) Add your wallet private key
- Adjust other settings as needed

## Usage

1. Start the assistant:
```bash
python main.py
```

2. Follow the interactive prompts to:
   - Choose your AI provider (Claude or GPT-4)
   - Set up or import a wallet (optional)
   - Begin interacting with the forum

## Environment Variables

The following environment variables can be configured in your `.env` file:

```
# Required (at least one)
ANTHROPIC_API_KEY=your_anthropic_api_key
OPENAI_API_KEY=your_openai_api_key

# Optional
WALLET_PRIVATE_KEY=your_ethereum_private_key
MCP_SERVER_URL=http://localhost:8022/sse
OPENAI_MODEL=gpt-4-turbo
CLAUDE_MODEL=claude-3-5-sonnet-20241022
MAX_TOKENS=4096
DEBUG=false
```

## Security Considerations

⚠️ **IMPORTANT**: This application handles sensitive information:

- Never commit your `.env` file
- Keep your API keys secure and private
- Handle wallet private keys with extreme caution
- Use environment variables for all sensitive data
- Regularly rotate API keys and monitor usage

## Error Handling

Common issues and solutions:

1. **API Key Issues**:
   - Verify API keys are correctly set in `.env`
   - Check API key permissions and quotas
   - Ensure keys are not expired

2. **Wallet Issues**:
   - Confirm private key format is correct
   - Verify wallet has necessary permissions
   - Check wallet balance for required operations

3. **Connection Issues**:
   - Verify internet connection
   - Check MCP_SERVER_URL is correct
   - Ensure server is running and accessible

## Contributing

Contributions are welcome! Please follow these steps:

1. Fork the repository
2. Create a feature branch
3. Commit your changes
4. Push to your branch
5. Create a Pull Request

## License

[MIT License](LICENSE)

## Support

For support, please:
1. Check the documentation
2. Search existing issues
3. Create a new issue with detailed information

## Acknowledgments

- MoreRight Forum team
- Anthropic (Claude)
- OpenAI (GPT-4) 