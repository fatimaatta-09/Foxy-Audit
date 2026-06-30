.PHONY: demo

demo:
	@echo "Starting Foxy Audit Backend Demo..."
	cd backend && docker compose up --build -d
	@echo "Backend is running at http://localhost:8000"
	@echo "API Key (if seeded): acme_test_key"
