# Optional shortcuts. Every target is a plain `docker compose` command
# (see README.md), so the Makefile is never required.
.PHONY: up down build logs migrate makemigrations test shell dbshell createsuperuser check backup restore ps

up:            ## Start the dev stack in the background
	docker compose up -d
build:         ## Rebuild images and start
	docker compose up --build -d
down:          ## Stop and remove containers (data in the volume is KEPT)
	docker compose down
ps:
	docker compose ps
logs:
	docker compose logs -f api
migrate:
	docker compose exec api python manage.py migrate
makemigrations:
	docker compose exec api python manage.py makemigrations
test:
	docker compose exec api pytest
check:
	docker compose exec api python manage.py check
shell:         ## Django shell
	docker compose exec api python manage.py shell
dbshell:       ## psql as the application role
	docker compose exec db sh -c 'psql -U "$$APP_DB_USER" -d "$$POSTGRES_DB"'
createsuperuser:
	docker compose exec api python manage.py createsuperuser
backup:
	./scripts/backup.sh
restore:       ## make restore FILE=backups/x.dump [DB=target_db]
	./scripts/restore.sh "$(FILE)" $(DB)
