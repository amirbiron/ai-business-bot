
# AI Business Chatbot

[![Python Version](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A complete, runnable AI-powered business chatbot system in Python. This project provides a template for businesses to create their own customer service chatbot with a Telegram interface, a RAG-based knowledge engine, and a web admin panel for easy management.

The system is designed with a three-layer architecture to ensure responses are accurate, relevant, and grounded in the business's actual knowledge base.

![Admin Panel Screenshot](https://i.imgur.com/example.png) <!-- Placeholder for a real screenshot -->

## Features

- **Telegram Bot Interface**: Customers interact with the bot in a natural, conversational way on Telegram.
- **Retrieval-Augmented Generation (RAG)**: The bot answers questions based on a local knowledge base, ensuring accuracy and preventing confabulation.
- **Web Admin Panel**: A simple, secure web interface for business owners to manage the knowledge base, view conversations, and handle customer requests.
- **Source-Cited Answers**: The bot is required to cite its sources, providing transparency and a mechanism for quality control.
- **Key Business Actions**: Pre-built buttons for common actions like "Book Appointment", "Price List", "Send Location", and "Talk to Agent".
- **Conversation History**: The bot maintains context for each user, allowing for more natural follow-up questions.
- **Notifications**: Business owners receive real-time Telegram notifications for agent requests and new appointment bookings.
- **Easy Setup & Deployment**: The project is designed to be easy to set up and run with minimal configuration.

## Architecture

The system is built on a three-layer architecture to ensure high-quality, reliable responses:

### Layer A: System & Behavior

This is the core personality and rule set of the bot. A system prompt (`config.py`) defines how the bot should behave:

> "You are a friendly and professional customer service representative for [Business Name]. ONLY answer based on the provided context information. NEVER make up information. If the context does not contain enough information, say you'll transfer to a human agent..."

This layer ensures the bot stays on-brand and follows critical operational rules.

### Layer B: Context & Retrieval (RAG)

Instead of stuffing all business information into a massive prompt, the system uses a lightweight RAG pipeline:

1.  **Knowledge Base**: Business information (services, prices, policies, FAQ, etc.) is stored in a local SQLite database.
2.  **Chunking**: Each KB entry is split into small, semantically meaningful chunks.
3.  **Embedding**: Each chunk is converted into a vector embedding using OpenAI's `text-embedding-3-small` model.
4.  **Vector Store**: The embeddings are stored in a FAISS index for efficient similarity search.
5.  **Retrieval**: When a customer asks a question, the system embeds the query and searches the FAISS index to find the 5-15 most relevant chunks.
6.  **Injection**: Only these relevant chunks are injected into the prompt sent to the LLM, providing focused context for the answer.

This approach is efficient, scalable, and dramatically improves the relevance and accuracy of the bot's answers.

### Layer C: Quality Check

To ensure the bot adheres to the "answer only from sources" rule, a simple but effective quality check is performed on every response:

1.  The system prompt requires the LLM to cite its source (e.g., "Source: Summer 2025 Price List").
2.  After the LLM generates a response, a Regex check (`llm.py`) verifies that a source citation is present.
3.  If the citation is missing, the response is discarded, and a safe fallback message is sent instead: "I don't have that information right now. Let me transfer you to a human agent..."

This acts as a final guardrail, preventing the bot from providing unverified information.

## Tech Stack

-   **Backend**: Python 3.9+
-   **Telegram Bot**: `python-telegram-bot`
-   **Web Admin Panel**: Flask
-   **LLM**: OpenAI API (`gpt-4.1-mini`)
-   **RAG / Vector Store**: FAISS (`faiss-cpu`)
-   **Database**: SQLite
-   **Configuration**: `python-dotenv`

## Project Structure

```
./
├── admin/                # Flask Web Admin Panel
│   ├── templates/        # HTML templates
│   ├── static/           # CSS, JS files
│   └── app.py            # Flask application
├── bot/                  # Telegram Bot
│   ├── handlers.py       # Command and message handlers
│   └── telegram_bot.py   # Bot setup and runner
├── data/                 # Data files (database, FAISS index)
├── rag/                  # RAG Engine
│   ├── chunker.py        # Text chunking logic
│   ├── embeddings.py     # OpenAI embedding generation
│   ├── engine.py         # RAG pipeline orchestration
│   └── vector_store.py   # FAISS index management
├── utils/                # Utility functions (TBD)
├── __init__.py
├── __main__.py           # Main entry point for running the app
├── config.py             # All configuration settings
├── database.py           # SQLite database management
├── llm.py                # LLM integration (Layers A, B, C)
├── main.py               # Main script to run bot/admin
├── seed_data.py          # Script to seed demo data
├── requirements.txt      # Python dependencies
└── .env.example          # Example environment variables file
```

## Setup and Installation

Follow these steps to get the chatbot system running locally.

### 1. Prerequisites

-   Python 3.9 or higher
-   An OpenAI API key
-   A Telegram Bot Token from BotFather
-   Your personal Telegram Chat ID (for notifications)

### 2. Clone the Repository

```bash
git clone https://github.com/amirbiron/ai-business-bot.git
cd ai-business-bot
```

### 3. Install Dependencies

Create a virtual environment and install the required packages:

```bash
python -m venv venv
source venv/bin/activate  # On Windows, use `venv\Scripts\activate`
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Copy the example `.env` file and fill in your credentials:

```bash
cp .env.example .env
```

Now, edit the `.env` file:

```dotenv
# Get this from BotFather on Telegram
TELEGRAM_BOT_TOKEN="YOUR_TELEGRAM_BOT_TOKEN"

# Get your personal chat ID from a bot like @userinfobot
TELEGRAM_OWNER_CHAT_ID="YOUR_TELEGRAM_CHAT_ID"

# Credentials for the web admin login
ADMIN_USERNAME="admin"
ADMIN_PASSWORD="your_secure_password"

# Secret key for Flask session management (change to a random string)
ADMIN_SECRET_KEY="a_very_long_and_random_string_for_security"

# The name of your business
BUSINESS_NAME="Dana's Beauty Salon"
```

### 5. Seed the Database and Build the Index

The project comes with demo data for a fictional business, "Dana's Beauty Salon". Run the seed script to populate the database and create the first RAG index.

```bash
python -m main --seed
```

This will:
1.  Create the `chatbot.db` SQLite file.
2.  Populate it with services, prices, policies, etc.
3.  Create chunks, generate embeddings, and build the `faiss_index`.

## Running the Application

You can run the Telegram bot and the web admin panel together or separately.

### Run Both (Bot + Admin Panel)

This is the default mode. It starts the Flask admin panel in a background thread and the Telegram bot in the main thread.

```bash
python -m main
```

-   **Telegram Bot**: Will start polling for messages.
-   **Admin Panel**: Will be available at `http://0.0.0.0:5000`.

### Run Only the Telegram Bot

```bash
python -m main --bot
```

### Run Only the Admin Panel

```bash
python -m main --admin
```

## How to Use

### 1. Talk to the Bot

Open Telegram and start a conversation with the bot you created with BotFather. You can ask it questions like:

-   "What are your hours on Wednesday?"
-   "How much is a men's haircut?"
-   "Do you do balayage?"
-   "What is your cancellation policy?"

Use the buttons to book an appointment, see the price list, or get the location.

### 2. Use the Admin Panel

Navigate to `http://localhost:5000` in your browser. Log in with the `ADMIN_USERNAME` and `ADMIN_PASSWORD` you set in your `.env` file.

From the admin panel, you can:

-   **Dashboard**: See at-a-glance statistics.
-   **Knowledge Base**: Add, edit, or delete business information. **Remember to click "Rebuild Index"** after making changes to update the bot's knowledge.
-   **Conversations**: View logs of all conversations with the bot.
-   **Agent Requests**: See a list of customers who have asked to speak to a human.
-   **Appointments**: Manage incoming appointment requests.

## Pushing to GitHub

This project is ready to be pushed to your own GitHub repository.

```bash
# Make sure you are in the project root directory

# Add all the new files
git add .

# Commit the changes
git commit -m "feat: Add initial AI business chatbot project"

# Push to your repository
git push origin main
```

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

