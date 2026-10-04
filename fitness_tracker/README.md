# FormLog - Smart Fitness & Nutrition Tracker (Flask + Gemini AI)
## Setup (Windows Command Prompt, inside this folder)
    python -m pip install -r requirements.txt
    set GEMINI_API_KEY=your-key-here
    python app.py
Open http://127.0.0.1:5000

## AI food scan
Uses the free Google Gemini API (gemini-2.5-flash) to identify foods in a photo or read a
nutrition label, estimate grams and macros, and add them automatically. Portions can be
corrected in "Today's meals". Free key: https://aistudio.google.com/apikey
Key: set GEMINI_API_KEY, or paste it alone into a file named gemini_key.txt next to app.py.
Without a key it falls back to guessing from the filename.
