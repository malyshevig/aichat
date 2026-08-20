
export DB="postgresql://ilia:begemot@gek:5432/aichat"

docker run -d  --network host --env DB_URL="$DB" --name ai-chat ai-chet:1.0.0 
