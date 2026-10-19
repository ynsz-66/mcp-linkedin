.PHONY: all up logs down clean help

all: help

up:
	@docker compose up --build -d

logs:
	docker compose logs -f

down:
	docker compose down

clean:
	docker compose down --rmi local

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  up      Build and start the containers in the background"
	@echo "  logs    Follow the container logs"
	@echo "  down    Stop and remove the containers"
	@echo "  clean   down + remove the locally built image"
	@echo "  help    Show this help"
