.PHONY: help install test lint run run-cycle-dry scheduler dashboard clean

help:
	@echo "AI Paper Trader - Available Commands"
	@echo "===================================="
	@echo "  make install        Install dependencies"
	@echo "  make test           Run test suite"
	@echo "  make lint           Run linting checks"
	@echo "  make run            Start the scheduler (live trading)"
	@echo "  make run-cycle-dry  Run one full trading cycle in dry-run mode (test at night)"
	@echo "  make scheduler      Alias for 'make run'"
	@echo "  make dashboard      Start the Streamlit dashboard"
	@echo "  make clean          Clean up __pycache__ and .pyc files"

install:
	pip install -r requirements.txt

test:
	pytest tests/ -v

lint:
	pylint fetchers/ engine/ risk/ executor/ feedback/ db/ scheduler/

run: scheduler

scheduler:
	python scheduler/loop.py

run-cycle-dry:
	python -c "from scheduler.loop import run_trading_cycle; import json; result = run_trading_cycle(is_premarket=True); print(json.dumps(result, indent=2, default=str))" | tee dry_run_$(shell date +%Y%m%d_%H%M%S).log

dashboard:
	streamlit run dashboard/app.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
