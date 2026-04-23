# LogiRoute AI Optimizer

**Author:** [Your Name]
**Status:** Core Engine Prototype (v1.0)

## 🎯 Overview
LogiRoute is a hybrid Decision Support System (DSS) designed for automated fuel logistics. It bridges the gap between high-level AI commands and rigorous mathematical optimization.

## 🚀 Key Features
* **Mathematical Core:** Powered by Google OR-Tools (VRPTW - Vehicle Routing Problem with Time Windows).
* **Triple-Layer Filtering:**
    1.  **Nominal Filter:** Automatically excludes sites with sufficient fuel (>80%).
    2.  **Policy Filter:** Skips sites under maintenance or decommissioning (Business Rules).
    3.  **Dispatcher Overrides:** Real-time AI-driven blocking/forcing via Natural Language.
* **Optimization Modes:**
    * **STRICT:** Ensures all priority sites are met within narrow time windows.
    * **FALLBACK:** Intelligent "best-effort" routing that sacrifices low-priority sites to save critical ones if constraints are too tight.
* **Analytics:** Detailed JSON diagnostics explaining why every single site was either routed or dropped.

## 🛠 Tech Stack
* **Backend:** Python 3.10+
* **Solver:** Google OR-Tools
* **API:** FastAPI / Uvicorn
* **AI Integration:** OpenRouter (Llama 3 / Mistral)
* **Frontend:** Vanilla JS / Leaflet.js (Modern SaaS UI)
