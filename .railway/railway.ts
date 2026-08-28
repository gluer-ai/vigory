// Railway Infrastructure as Code — defines the 3 services for Vigory.ai.
// See DEPLOYMENT.md for the full setup/apply sequence (secrets must be set
// manually the first time; nothing sensitive is ever written to this file).
//
// Apply with:
//   railway login && railway link
//   railway config plan     # preview
//   railway config apply    # apply after confirmation
import {
  defineRailway,
  github,
  group,
  image,
  preserve,
  project,
  service,
  volume,
} from "railway/iac";

const REPO = "gluer-ai/vigory";

export default defineRailway(() => {
  const neo4jData = volume("neo4j-data", { sizeMB: 2048 });

  // Private-only: no `domains`, so it's never reachable from the public
  // internet — only over Railway's internal network as neo4j.railway.internal.
  const neo4j = service("neo4j", {
    source: image("neo4j:5.26-community"),
    volumeMounts: { "/data": neo4jData },
    env: {
      // Set once via `railway variables --service neo4j --set NEO4J_PASSWORD=... --set NEO4J_AUTH=neo4j/...`
      // (see DEPLOYMENT.md) — preserve() keeps whatever is already set rather
      // than ever writing a plaintext password into this committed file.
      NEO4J_PASSWORD: preserve(),
      NEO4J_AUTH: preserve(),
      NEO4J_PLUGINS: "[]",
    },
  });

  const backend = service("backend", {
    source: github(REPO, { rootDirectory: "backend" }),
    healthcheck: "/health",
    healthcheckTimeout: 30,
    env: {
      // neo4j.railway.internal is deterministic from the service name
      // "neo4j" above — update this if that service is ever renamed.
      NEO4J_URI: "bolt://neo4j.railway.internal:7687",
      NEO4J_USER: "neo4j",
      NEO4J_PASSWORD: neo4j.env.NEO4J_PASSWORD,
      LLM_PROVIDER: "openai",
      LLM_MODEL: "gpt-4o",
      OPENAI_API_KEY: preserve(),
      ANTHROPIC_API_KEY: preserve(),
      // Set to the frontend's public URL once it has one (see DEPLOYMENT.md)
      // — defaults to "*" (open) only until you set this.
      CORS_ALLOWED_ORIGINS: preserve(),
    },
  });

  const frontend = service("frontend", {
    source: github(REPO, { rootDirectory: "frontend" }),
    env: {
      // Must be the backend's public URL, e.g. https://backend-xxxx.up.railway.app
      // Vite bakes this in at build time, so it must be set BEFORE the first
      // frontend build — see DEPLOYMENT.md's two-phase deploy order.
      VITE_API_BASE_URL: preserve(),
    },
  });

  return project("vigory-ai", {
    resources: [group("Vigory.ai", [neo4jData, neo4j, backend, frontend])],
  });
});
