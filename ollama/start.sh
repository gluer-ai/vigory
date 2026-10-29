#!/bin/sh
# Start the Ollama server, make sure ONLY ${OLLAMA_MODEL} is on the persistent
# volume (/root/.ollama), then keep the server in the foreground.
set -eu

# A download interrupted earlier (e.g. disk full) leaves multi-GB *-partial
# files that nothing else cleans up; clear them so they can't fill the volume.
rm -f /root/.ollama/models/blobs/*-partial*

ollama serve &
SERVER_PID=$!

until ollama list >/dev/null 2>&1; do sleep 1; done

# Remove any previously pulled model other than the configured one.
ollama list | awk 'NR>1 {print $1}' | while read -r name; do
  if [ "$name" != "${OLLAMA_MODEL}" ]; then
    echo "Removing ${name}"
    ollama rm "$name"
  fi
done

if ! ollama list | grep -q "^${OLLAMA_MODEL}"; then
  echo "Pulling ${OLLAMA_MODEL}..."
  ollama pull "${OLLAMA_MODEL}"
fi

echo "Ready: ${OLLAMA_MODEL}"
wait "$SERVER_PID"
