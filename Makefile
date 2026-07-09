.PHONY: demo

demo:
	@echo "Starting Foxy Audit Backend Demo..."
	cd backend && docker compose up --build -d
	@echo "Backend is running at http://localhost:8000"
	@echo "API Key: check the backend seed output for the demo key"
