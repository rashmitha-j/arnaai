#!/usr/bin/env bash
# Deploy ArnaAI to Google Cloud Run as three microservices.
#
#   ./deploy/gcp/deploy.sh <PROJECT_ID> [REGION]
#
# Creates (idempotently): Artifact Registry repo, Firestore DB, service
# accounts with least-privilege roles, Secret Manager secrets, and three
# Cloud Run services:
#   arnaai-emotion  private  (only the therapy service may invoke it)
#   arnaai-therapy  public   (own auth: per-session tokens + counselor token)
#   arnaai-web      public   (Next.js frontend, proxies /api to therapy)
set -euo pipefail

PROJECT="${1:?usage: deploy.sh PROJECT_ID [REGION]}"
REGION="${2:-asia-south1}"
REPO="arnaai"
TAG="$(date +%Y%m%d-%H%M%S)"
AR="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# Cost: scale to zero when idle (first request after idle is a slow cold start)
# and cap instances. Raise MIN_INSTANCES / MAX_INSTANCES for real traffic.
MIN_INSTANCES="${MIN_INSTANCES:-0}"
MAX_INSTANCES="${MAX_INSTANCES:-2}"

gcloud config set project "$PROJECT" >/dev/null
echo "› Enabling APIs"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com firestore.googleapis.com aiplatform.googleapis.com iam.googleapis.com

echo "› Artifact Registry + Firestore"
gcloud artifacts repositories describe "$REPO" --location "$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create "$REPO" --repository-format docker --location "$REGION"
gcloud firestore databases describe --database="(default)" >/dev/null 2>&1 || \
  gcloud firestore databases create --location "$REGION" --type firestore-native

echo "› Service accounts"
for sa in arnaai-emotion arnaai-therapy arnaai-web; do
  gcloud iam service-accounts describe "${sa}@${PROJECT}.iam.gserviceaccount.com" >/dev/null 2>&1 || \
    gcloud iam service-accounts create "$sa" --display-name "$sa"
done
THERAPY_SA="arnaai-therapy@${PROJECT}.iam.gserviceaccount.com"
for role in roles/datastore.user roles/aiplatform.user roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:${THERAPY_SA}" --role "$role" \
    --condition=None >/dev/null
done

echo "› Secrets (created once; rotate with 'gcloud secrets versions add')"
make_secret() { # name value
  gcloud secrets describe "$1" >/dev/null 2>&1 || printf '%s' "$2" | gcloud secrets create "$1" --data-file=- --replication-policy automatic
}
# Fernet key = url-safe base64 of 32 random bytes (openssl avoids needing Python here).
make_secret arnaai-data-key "$(openssl rand 32 | base64 | tr '+/' '-_')"
make_secret arnaai-counselor-token "$(openssl rand -hex 24)"
make_secret arnaai-service-key "$(openssl rand -hex 24)"
if [[ -n "${GOOGLE_API_KEY:-}" ]]; then make_secret arnaai-google-api-key "$GOOGLE_API_KEY"; fi

echo "› Building images with Cloud Build"
for svc in emotion therapy; do
  gcloud builds submit "$ROOT/services/$svc" --tag "$AR/$svc:$TAG" --quiet
done
gcloud builds submit "$ROOT/frontend" --tag "$AR/web:$TAG" --quiet

echo "› Deploying emotion service (private)"
gcloud run deploy arnaai-emotion --image "$AR/emotion:$TAG" --region "$REGION" \
  --service-account "arnaai-emotion@${PROJECT}.iam.gserviceaccount.com" \
  --no-allow-unauthenticated --cpu 1 --memory 512Mi --min-instances "$MIN_INSTANCES" --max-instances "$MAX_INSTANCES" \
  --set-secrets SERVICE_KEY=arnaai-service-key:latest --quiet
EMOTION_URL="$(gcloud run services describe arnaai-emotion --region "$REGION" --format 'value(status.url)')"
gcloud run services add-iam-policy-binding arnaai-emotion --region "$REGION" \
  --member "serviceAccount:${THERAPY_SA}" --role roles/run.invoker >/dev/null

echo "› Deploying therapy engine"
LLM_ENV="LLM_PROVIDER=vertex,GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${REGION}"
SECRETS="DATA_ENCRYPTION_KEY=arnaai-data-key:latest,COUNSELOR_TOKEN=arnaai-counselor-token:latest,SERVICE_KEY=arnaai-service-key:latest"
if gcloud secrets describe arnaai-google-api-key >/dev/null 2>&1; then
  LLM_ENV="LLM_PROVIDER=gemini"; SECRETS="${SECRETS},GOOGLE_API_KEY=arnaai-google-api-key:latest"
fi
gcloud run deploy arnaai-therapy --image "$AR/therapy:$TAG" --region "$REGION" \
  --service-account "$THERAPY_SA" --allow-unauthenticated --cpu 1 --memory 1Gi \
  --min-instances "$MIN_INSTANCES" --max-instances "$MAX_INSTANCES" --concurrency 40 \
  --set-env-vars "STORAGE_BACKEND=firestore,EMOTION_SERVICE_URL=${EMOTION_URL},GEMINI_MODEL=${GEMINI_MODEL:-gemini-3.8-flash},EXTRACTION_MODEL=${EXTRACTION_MODEL:-gemini-3.5-flash-lite},${LLM_ENV}" \
  --set-secrets "$SECRETS" --quiet
THERAPY_URL="$(gcloud run services describe arnaai-therapy --region "$REGION" --format 'value(status.url)')"

echo "› Deploying web frontend"
gcloud run deploy arnaai-web --image "$AR/web:$TAG" --region "$REGION" \
  --service-account "arnaai-web@${PROJECT}.iam.gserviceaccount.com" \
  --allow-unauthenticated --cpu 1 --memory 512Mi --min-instances "$MIN_INSTANCES" --max-instances "$MAX_INSTANCES" \
  --set-env-vars "THERAPY_API_URL=${THERAPY_URL}" --quiet
WEB_URL="$(gcloud run services describe arnaai-web --region "$REGION" --format 'value(status.url)')"

# Lock CORS and alert links to the real frontend URL.
gcloud run services update arnaai-therapy --region "$REGION" \
  --update-env-vars "ALLOWED_ORIGINS=${WEB_URL},PUBLIC_APP_URL=${WEB_URL}" --quiet

echo
echo "✓ ArnaAI is live: ${WEB_URL}"
echo "  Counselor dashboard: ${WEB_URL}/counselor"
echo "  Counselor access code: gcloud secrets versions access latest --secret arnaai-counselor-token"
