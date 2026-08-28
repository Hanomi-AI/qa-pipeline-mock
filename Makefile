.PHONY: up down logs reset test smoke ps

up:            ## start everything
	docker compose up --build -d
	@echo "api      http://localhost:8080/docs"
	@echo "ui       http://localhost:3000"
	@echo "rabbitmq http://localhost:15672  (guest/guest)"

down:
	docker compose down -v

logs:
	docker compose logs -f backend

ps:
	docker compose ps

reset:         ## wipe db + queues and restart
	docker compose down -v && docker compose up --build -d

smoke:         ## submit a batch and print the outcome
	python3 scripts/smoke.py

test:
	@echo "This service is the system under test - the tests are yours to write."
	@echo "Run 'make smoke' to confirm the pipeline is alive."
