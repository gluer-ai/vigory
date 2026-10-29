FROM ollama/ollama:latest

ENV OLLAMA_HOST=0.0.0.0:11434 \
    OLLAMA_KEEP_ALIVE=-1 \
    OLLAMA_MODEL=qwen3:4b

COPY start.sh /start.sh
RUN chmod +x /start.sh

EXPOSE 11434
ENTRYPOINT ["/start.sh"]
