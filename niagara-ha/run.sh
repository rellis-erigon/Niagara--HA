#!/usr/bin/with-contenv bashio

bashio::log.info "Starting Niagara BMS Bridge..."

exec python3 /app/src/main.py
