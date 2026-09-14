.PHONY: setup check run test docker

setup:
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt
	cp -n .env.example .env || true
	@echo "Now fill in DISCORD_TOKEN and DISCORD_CHANNEL_ID in .env"

check:
	python3 -m newsbot check

run:
	python3 -m newsbot

test:
	python3 -m pytest -q

docker:
	docker compose up -d --build
