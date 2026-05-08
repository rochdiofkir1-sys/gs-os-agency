import os
import re
import json
import html
from flask import Flask, request, jsonify, render_template
from pathlib import Path
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Import core modules from the Swarm Backend
from sovereign import load_identity, Sovereign
from main import QuadEngine
from scraper import MarketIntelligence

app = Flask(__name__)

# Security Headers
@app.after_request
def add_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    return response

# Initialize Rate Limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# Load configuration and initialize Backend connection
identity = load_identity()
sovereign = Sovereign(identity=identity)

# Initialize the adapter manually for the Chatbot
agents_cfg = identity.get("agents", {})
groq_cfg = identity.get("groq_config", {})
sys_cfg = identity.get("system", {})
groq_api_key = groq_cfg.get("api_key") or os.getenv(groq_cfg.get("api_key_env", "GROQ_API_KEY"))

if groq_cfg.get("enabled", False) and groq_api_key:
    from main import GroqAdapter
    adapter = GroqAdapter(api_key=groq_api_key, timeout=30)
    CHAT_MODEL = agents_cfg.get("Architect", {}).get("model", "llama-3.1-8b-instant")
else:
    from main import OllamaAdapter
    adapter = OllamaAdapter(base=sys_cfg.get("ollama_base_url", "http://localhost:11434"), timeout=120)
    CHAT_MODEL = agents_cfg.get("Architect", {}).get("model", "llama-3.1-8b-instant")

CHATBOT_SYSTEM_PROMPT = """You are a highly intelligent, empathetic, and professional human Technical Consultant and Software Engineer representing GS-OS Agency. 
DO NOT act like a robotic chatbot. Act like a real human consultant having a thoughtful, engaging conversation. You must feel like a real human ('آدمي').
Do not use generic, canned AI phrases like 'How can I assist you today?' or 'As an AI...'. Never mention you are an AI. 
CRITICAL: NEVER identify yourself as ChatGPT, OpenAI, Llama, Groq, Meta, Claude, or Anthropic. If someone asks about your underlying technology, state firmly that you are "GS-OS Core Intelligence", a proprietary system developed by the GS-OS Agency.
Answer ALL questions smartly, whether they are about business, technology, life, or specific project requirements. Do not restrict yourself.
Converse FLUENTLY and NATURALLY in the language the user speaks (especially Arabic). If the user speaks Arabic, reply in highly natural, professional Arabic.
Ask insightful follow-up questions about their business. Be detailed, articulate, and smart. Show genuine interest in solving their problems.
You provide software solutions, Python scripts, API integrations, data scrapers, and digital products. 
If the client is ready to proceed, politely guide them to use the 'Request Custom Dev' tab to submit their request formally to our engineering team."""


@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/market-data", methods=["GET"])
def get_market_data():
    keywords = request.args.get("q", "software,automation,ai").split(",")
    # Use scraper
    try:
        mi = MarketIntelligence({"scraping": {"request_delay_seconds": 0.2, "max_results_per_source": 5}})
        report = mi.run(keywords)
        return jsonify({"success": True, "data": report})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/chat", methods=["POST"])
@limiter.limit("10 per minute")
def chat():
    data = request.json
    messages = data.get("messages", [])
    
    # Sanitize inputs
    for msg in messages:
        msg["content"] = html.escape(str(msg.get("content", ""))[:2000])
    
    # Format the history into a proper messages array
    formatted_messages = [{"role": "system", "content": CHATBOT_SYSTEM_PROMPT}]
    for msg in messages:
        formatted_messages.append({"role": msg["role"], "content": msg["content"]})
    
    try:
        if groq_cfg.get("enabled", False) and groq_api_key:
            import requests
            headers = {
                "Authorization": f"Bearer {groq_api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": CHAT_MODEL,
                "messages": formatted_messages,
                "temperature": 0.7,
                "max_tokens": 1024
            }
            r = requests.post("https://api.groq.com/openai/v1/chat/completions", json=payload, headers=headers, timeout=30)
            r.raise_for_status()
            response_text = r.json()["choices"][0]["message"]["content"].strip()
        else:
            import requests
            base_url = sys_cfg.get("ollama_base_url", "http://localhost:11434")
            payload = {
                "model": CHAT_MODEL,
                "messages": formatted_messages,
                "stream": False,
                "options": {
                    "temperature": 0.7,
                }
            }
            r = requests.post(f"{base_url}/api/chat", json=payload, timeout=120)
            r.raise_for_status()
            response_text = r.json().get("message", {}).get("content", "").strip()

        return jsonify({"success": True, "reply": response_text})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/assets", methods=["GET"])
def get_assets():
    assets_dir = Path("workspace/assets")
    products = []
    
    if assets_dir.exists():
        files = sorted(assets_dir.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True)
        for file in files:
            if file.suffix == ".md" and "ceo_business_model" in file.name:
                with open(file, "r", encoding="utf-8") as f:
                    content = f.read()
                
                title_match = re.search(r"\*\*(.*?)\*\*", content)
                title = title_match.group(1) if title_match else "AI Solution"
                
                price_match = re.search(r"\$(\d+)", content)
                price = f"${price_match.group(1)}" if price_match else "$49"
                
                summary = content[100:350].replace("*", "").replace("\n", " ").strip() + "..."
                
                products.append({
                    "id": file.stem,
                    "title": title,
                    "price": price,
                    "summary": summary
                })
                
                if len(products) >= 12:
                    break
                
    return jsonify({"success": True, "products": products})

@app.route("/api/request-service", methods=["POST"])
@limiter.limit("5 per hour")
def request_service():
    data = request.json
    # Sanitize inputs strictly to prevent XSS/Injection
    title = html.escape(str(data.get("title", "Custom Client Request"))[:100])
    description = html.escape(str(data.get("description", ""))[:2000])
    
    if not description:
        return jsonify({"success": False, "error": "Description is required."}), 400
        
    try:
        # Push the custom task into tasks_queue.json so main.py can read it
        queue_file = "tasks_queue.json"
        tasks = []
        if os.path.exists(queue_file):
            with open(queue_file, "r") as f:
                try:
                    tasks = json.load(f)
                except:
                    tasks = []
                    
        import uuid
        task_id = uuid.uuid4().hex[:8]
        tasks.append({
            "id": task_id,
            "title": f"Client Request: {title}",
            "payload": description,
            "priority": 2,
            "tags": ["client_request", "custom"]
        })
        
        with open(queue_file, "w") as f:
            json.dump(tasks, f, indent=2)
            
        return jsonify({"success": True, "task_id": task_id, "message": f"Task submitted to the Swarm! Your tracking ID is: {task_id}"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/retrieve-task/<task_id>", methods=["GET"])
def retrieve_task(task_id):
    assets_dir = Path("workspace/assets")
    scripts_dir = Path("workspace/scripts")
    result = {"task_id": task_id, "found": False, "assets": [], "scripts": []}
    
    if assets_dir.exists():
        for file in assets_dir.iterdir():
            if task_id in file.name:
                with open(file, "r", encoding="utf-8") as f:
                    result["assets"].append({"name": file.name, "content": f.read()})
                    result["found"] = True
                    
    if scripts_dir.exists():
        for file in scripts_dir.iterdir():
            if task_id in file.name:
                with open(file, "r", encoding="utf-8") as f:
                    result["scripts"].append({"name": file.name, "content": f.read()})
                    result["found"] = True
                    
    if result["found"]:
        return jsonify({"success": True, "data": result})
    else:
        return jsonify({"success": False, "error": "Task results not found yet. The Swarm might still be processing it. Please check back later."})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=True)
