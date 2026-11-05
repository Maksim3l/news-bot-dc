.PHONY: setup config check digest run test docker

setup: config
	python3 -m venv .venv
	.venv/bin/pip install -r requirements.txt

# Local copies of the templates. Existing files are never overwritten.
config:
	cp -n .env.example .env || true
	cp -n sources.example.yml sources.yml || true
	cp -n digest.example.yml digest.yml || true
	@echo "Now fill in DISCORD_TOKEN and DISCORD_CHANNEL_ID in .env"

check:
	python3 -m newsbot check

digest:
	python3 -m newsbot digest

run:
	python3 -m newsbot

test:
	python3 -m pytest -q

docker: config
	docker compose up -d --build
