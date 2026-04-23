"""
Logistics Orchestration Server.

This module serves as the primary entry point for the fuel delivery system, 
managing the execution pipeline from telemetry parsing to route optimization.
It utilizes FastAPI for the web interface and OpenRouter for AI-driven commands.
"""

import os
import json
import subprocess
from typing import Any, Dict

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Load environment variables from .env file
load_dotenv()

app = FastAPI()

# Enable Cross-Origin Resource Sharing for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# Constants for file paths and data management
DATA_DIR = "mock_data"
SOLUTION_FILE = os.path.join(DATA_DIR, "route_solution.json")
OVERRIDES_FILE = os.path.join(DATA_DIR, "dispatch_overrides.json")

# API Configuration for OpenRouter LLM
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "optional")


class CommandRequest(BaseModel):
    """Schema for natural language dispatcher commands."""
    text: str


@app.get("/get-solution")
def get_solution() -> Dict[str, Any]:
    """
    Retrieves the most recently calculated routing plan.
    """
    if os.path.exists(SOLUTION_FILE):
        with open(SOLUTION_FILE, "r") as f:
            return json.load(f)
    return {"status": "error", "message": "No solution found"}


@app.get("/get-telemetry")
def get_telemetry() -> Any:
    """
    Provides access to the current processed telemetry data.
    """
    telemetry_file = os.path.join(DATA_DIR, "telemetry.json")
    if os.path.exists(telemetry_file):
        with open(telemetry_file, "r") as f:
            return json.load(f)
    return []


@app.post("/init-daily-route")
def init_daily_route() -> Dict[str, Any]:
    """
    Orchestrates the full start-of-day pipeline.
    Resets overrides, parses telemetry, and calculates initial routes.
    """
    try:
        # Reset dispatcher overrides to a clean state
        empty_overrides = {"blocked_towers": [], "force_critical": []}
        with open(OVERRIDES_FILE, "w") as f:
            json.dump(empty_overrides, f, indent=4)
            
        # Execute the processing pipeline using the python3 interpreter and specific script paths
        subprocess.run(["python3", "scripts/parse_telemetry.py"], check=True)
        subprocess.run(["python3", "scripts/build_matrix.py"], check=True)
        subprocess.run(["python3", "scripts/full_solver_final.py"], check=True)
        
        with open(SOLUTION_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/run-solver")
def run_solver() -> Dict[str, Any]:
    """
    Re-calculates routes based on existing data and current manual overrides.
    """
    try:
        subprocess.run(["python3", "scripts/full_solver_final.py"], check=True)
        with open(SOLUTION_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/ai-command")
def process_ai_command(req: CommandRequest) -> Dict[str, Any]:
    """
    Interprets natural language input and converts it to system-level overrides.
    Uses LLM for intent classification and entity (Tower ID) extraction.
    """
    if not OPENROUTER_API_KEY:
        raise HTTPException(status_code=500, detail="OPENROUTER_API_KEY not found")

    system_instruction = """
    You are an intelligent logistics translator for a fuel delivery system.
    Your job is to extract tower IDs from the user's text and categorize them into a strict JSON format.
    Tower IDs always look like "TWR-042" or "TWR-100". If the user says "tower 42", map it to "TWR-042" (pad with zeros to 3 digits).
    
    Rules:
    - "blocked_towers": Towers that cannot be visited (road blocked, canceled, broken).
    - "force_critical": Towers that MUST be visited urgently (VIP event, storm warning).
    
    You MUST output ONLY valid JSON matching this schema exactly:
    {
        "blocked_towers": ["TWR-XXX"],
        "force_critical": ["TWR-YYY"]
    }
    No markdown formatting. No chatty text. Just raw JSON.
    """
    
    try:
        response = requests.post(
            url="https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": OPENROUTER_MODEL,
                "messages": [
                    {"role": "system", "content": system_instruction},
                    {"role": "user", "content": req.text}
                ],
                "temperature": 0.0
            }
        )
        response.raise_for_status()
        data = response.json()
        ai_result_text = data['choices'][0]['message']['content'].strip()
        
        # Strip potential markdown formatting if returned by the LLM
        if "```json" in ai_result_text:
            ai_result_text = ai_result_text.split("```json")[1].split("```")[0].strip()
        elif "```" in ai_result_text:
            ai_result_text = ai_result_text.split("```")[1].split("```")[0].strip()
            
        ai_json = json.loads(ai_result_text)
        
        # Update existing overrides instead of overwriting them
        current_overrides = {"blocked_towers": [], "force_critical": []}
        if os.path.exists(OVERRIDES_FILE):
             with open(OVERRIDES_FILE, "r") as f:
                 try: 
                     current_overrides = json.load(f)
                 except: 
                     pass
                 
        current_overrides["blocked_towers"] = list(set(
            current_overrides.get("blocked_towers", []) + ai_json.get("blocked_towers", [])
        ))
        current_overrides["force_critical"] = list(set(
            current_overrides.get("force_critical", []) + ai_json.get("force_critical", [])
        ))
        
        with open(OVERRIDES_FILE, "w") as f:
            json.dump(current_overrides, f, indent=4)
            
        return {"status": "success", "data": current_overrides}
        
    except Exception as e:
        print(f"AI Error: {e}")
        raise HTTPException(status_code=500, detail=f"AI processing failed: {str(e)}")


@app.get("/download-route")
def download_route() -> FileResponse:
    """
    Exports the final routing plan as a downloadable JSON file.
    """
    if os.path.exists(SOLUTION_FILE):
        return FileResponse(
            path=SOLUTION_FILE, 
            filename="final_routing_plan.json", 
            media_type="application/json"
        )
    raise HTTPException(status_code=404, detail="File not found")


if __name__ == "__main__":
    import uvicorn
    # Start the server with the specified parameters
    uvicorn.run(app, host="0.0.0.0", port=8000)