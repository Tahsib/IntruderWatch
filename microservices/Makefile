.PHONY: rebuild restart restart-monitoring reset-prometheus test-go test-python test

rebuild:
	docker compose down
	docker compose up --build -d

restart:
	docker compose down
	docker compose up -d

restart-monitoring:
	docker compose stop prometheus grafana
	docker compose rm -f prometheus grafana
	docker compose up -d --force-recreate prometheus grafana

reset-prometheus:
	./fix_prometheus_data.sh

test-go:
	cd frame_capturer && go test -v -race ./...

test-python:
	ruff check .
	ruff format --check .

test: test-go test-python
