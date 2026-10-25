# Shanex Dev
# AI Assistant with Google Gemini API

A clean, minimalist, lightweight AI assistant built with **Flask** and **Google Generative AI (Gemini 1.5 Flash)**. Perfect for quick deployment, embedding on platforms like **shanexdev.odoo.com**, and monetizing with ads!

---

## Features
- **Lightweight UI**: Fast, clean Tailwind CSS chat interface.
- **Google Gemini API**: Powered by `gemini-1.5-flash` for fast and intelligent responses.
- **Ad-Ready**: Pre-configured placeholders for banner ads/monetization.
- **Easy Setup**: Minimal Python requirements (`Flask`, `google-generativeai`, `python-dotenv`).

---

## Project Structure
```text
ai-assistant/
├── app.py                  # Main Flask application
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variables template
└── templates/
    └── index.html          # Chat interface & ad placeholders
```

---

## Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/YOUR_USERNAME/ai-assistant.git
   cd ai-assistant
   ```

2. **Create and activate a virtual environment:**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure your API Key:**
   Copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```
   Open `.env` and add your Google AI Studio API key:
   ```env
   GOOGLE_API_KEY=your_actual_google_api_key_here
   ```

5. **Run the application:**
   ```bash
   python app.py
   ```
   Open your browser at `http://127.0.0.1:5000`.

---

## Monetization (Adding Ads)
Inside [templates/index.html](templates/index.html), you will find designated ad spaces (e.g., Google AdSense, banner ads, or custom affiliate links) where you can easily drop your ad scripts to start earning money!

---

## License
MIT License. Free to use, modify, and monetize.
