# ---- build stage ----
FROM node:20-alpine AS build
WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Vite bakes VITE_-prefixed vars into the bundle at build time. Railway only
# forwards a service Variable into `docker build` if it's declared as an ARG
# here — set VITE_API_BASE_URL as a Variable on this service in Railway.
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL
RUN npm run build

# ---- run stage ----
FROM node:20-alpine
WORKDIR /app
RUN npm install -g serve@14
COPY --from=build /app/dist ./dist

# Railway injects $PORT at runtime; -s enables SPA fallback to index.html.
CMD ["sh", "-c", "serve -s dist -l ${PORT:-4173}"]
