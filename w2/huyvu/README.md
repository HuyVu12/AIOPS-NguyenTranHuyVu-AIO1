# Evidence-Driven Remediation Engine (Platform Ops)

This project implements an **evidence-driven remediation engine** designed to automatically suggest remediation actions for microservice incidents based on structural logs, traces, topology, and metrics compared against a historical incident corpus.

## Setup Instructions

Ensure you have Python 3.12+ installed.

1. **Set up virtual environment & install dependencies:**
   ```bash
   python -m venv .venv
   # On Windows (cmd):
   .venv\Scripts\activate
   # On Windows (PowerShell):
   .venv\Scripts\Activate.ps1
   # On Unix/macOS:
   source .venv/bin/activate

   # Install the required packages
   pip install pyyaml
   ```

## How to Run

To run the engine decision pipeline on a specific live incident:
```bash
python engine.py decide --incident eval/E01.json \
                        --history incidents_history.json \
                        --actions actions.yaml
```

This will print the recommended action JSON to standard output and append a corresponding audit entry to `audit.jsonl`.

## Running the Evaluation & Auto-Grader

To run the engine over all 8 evaluation incidents (`E01` to `E08`), clear the log and run the Python loop:

```bash
# Clean old audit logs
python -c "import os; os.path.exists('audit.jsonl') and os.remove('audit.jsonl')"

# Run loop (Windows / Unix / macOS cross-compatible)
python -c "import subprocess; [subprocess.run(['python', 'engine.py', 'decide', '--incident', f'eval/E{i:02d}.json']) for i in range(1, 9)]"
```

Once `audit.jsonl` is generated, run the auto-grader:
```bash
python grade.py --audit audit.jsonl --expected eval/expected.json
```

Expected result should be **8/8 correct** (85/85 points).
