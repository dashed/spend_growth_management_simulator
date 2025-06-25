.PHONY: help install test test-verbose test-coverage test-specific run clean lint format check-format type-check type-check-main update-deps

# Default target
help:
	@echo "SGM Simulator - Available commands:"
	@echo ""
	@echo "Setup & Running:"
	@echo "  make install        - Install dependencies"
	@echo "  make run           - Run the simulator"
	@echo "  make clean         - Clean up cache and temporary files"
	@echo ""
	@echo "Testing:"
	@echo "  make test          - Run all tests"
	@echo "  make test-verbose  - Run tests with verbose output"
	@echo "  make test-coverage - Run tests with coverage report"
	@echo "  make test-core     - Run core tests (engine, integration, regression)"
	@echo "  make test-advanced - Run advanced tests (comprehensive, stress, long-term)"
	@echo "  make test-specific TEST=<test_name> - Run specific test"
	@echo ""
	@echo "Individual Test Suites:"
	@echo "  make test-engine        - Core SGM engine tests"
	@echo "  make test-scenarios     - Basic scenario tests" 
	@echo "  make test-edge-cases    - Edge case tests"
	@echo "  make test-integration   - Integration tests"
	@echo "  make test-regression    - Regression tests"
	@echo "  make test-comprehensive - Comprehensive scenarios"
	@echo "  make test-reserved      - Reserved volume tests"
	@echo "  make test-wallet        - Wallet behavior tests"
	@echo "  make test-manual        - Manual allowance tests"
	@echo "  make test-long-term     - Long-term simulation tests"
	@echo "  make test-stress        - Stress and extreme value tests"
	@echo "  make test-business      - Business scenario tests"
	@echo ""
	@echo "Code Quality:"
	@echo "  make lint          - Run linting checks"
	@echo "  make format        - Auto-format code"
	@echo "  make check-format  - Check code formatting"
	@echo "  make type-check    - Run type checking on all Python files"
	@echo "  make type-check-main - Run type checking on sgm_simulator.py only"
	@echo "  make update-deps   - Update dependencies"

# Install dependencies
install:
	uv sync
	uv pip install -e ".[test]"

# Run all tests
test:
	uv run pytest

# Run tests with verbose output
test-verbose:
	uv run pytest -v

# Run tests with coverage
test-coverage:
	uv run pytest --cov=sgm_simulator --cov-report=html --cov-report=term
	@echo "Coverage report generated in htmlcov/index.html"

# Run specific test
test-specific:
	@if [ -z "$(TEST)" ]; then \
		echo "Usage: make test-specific TEST=test_name"; \
		echo "Example: make test-specific TEST=test_sgm_engine.py::TestSGMEngine::test_bootstrap_initial_limit"; \
		exit 1; \
	fi
	uv run pytest -v $(TEST)

# Run individual test files
test-engine:
	uv run pytest test_sgm_engine.py -v

test-scenarios:
	uv run pytest test_sgm_scenarios.py -v

test-edge-cases:
	uv run pytest test_sgm_edge_cases.py -v

test-integration:
	uv run pytest test_sgm_integration.py -v

test-regression:
	uv run pytest test_sgm_regression.py -v

test-comprehensive:
	uv run pytest test_sgm_comprehensive_scenarios.py -v

test-reserved:
	uv run pytest test_sgm_reserved_volumes.py -v

test-wallet:
	uv run pytest test_sgm_wallet_comprehensive.py -v

test-manual:
	uv run pytest test_sgm_manual_allowance.py -v

test-long-term:
	uv run pytest test_sgm_long_term.py -v

test-stress:
	uv run pytest test_sgm_stress.py -v

test-business:
	uv run pytest test_sgm_business_scenarios.py -v

# Run the simulator
run:
	uv run streamlit run sgm_simulator.py

# Run alternative simulator
run-alt:
	uv run streamlit run sgm_alt.py

# Clean up cache and temporary files
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name ".coverage" -delete
	rm -rf .streamlit/cache 2>/dev/null || true

# Linting
lint:
	uv run ruff check .
	uv run mypy sgm_simulator.py --ignore-missing-imports || true

# Format code
format:
	uv run ruff format .
	uv run ruff check --fix .

# Check formatting without changing files
check-format:
	uv run ruff format --check .
	uv run ruff check .

# Type checking
type-check:
	uvx ty check --exclude sgm_alt.py

# Type check only the main simulator file
type-check-main:
	uvx ty check sgm_simulator.py

# Update dependencies
update-deps:
	uv sync --upgrade

# Development setup
dev-setup: install
	uv pip install ruff mypy pytest-cov
	@echo "Development environment ready!"

# Quick test - run fast tests only
test-quick:
	uv run pytest -v -m "not slow" || uv run pytest -v -k "not test_full_month"

# Watch tests (requires pytest-watch)
test-watch:
	uv pip install pytest-watch
	uv run ptw -- -v

# Generate test report
test-report:
	uv run pytest --html=test_report.html --self-contained-html
	@echo "Test report generated: test_report.html"

# Check Python version
check-python:
	@python --version
	@echo "Using uv with Python:"
	@uv run python --version

# Full CI-like check
ci: clean check-format lint type-check test-coverage
	@echo "All CI checks passed!"

# Install pre-commit hooks (if using pre-commit)
install-hooks:
	uv pip install pre-commit
	uv run pre-commit install

# Run security checks
security-check:
	uv pip install bandit safety
	uv run bandit -r sgm_simulator.py -ll
	uv run safety check || true