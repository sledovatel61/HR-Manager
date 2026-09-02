# Dev-образ фронтенда (Этап 1): Vite dev-сервер с hot-reload.
FROM node:22-alpine

WORKDIR /app

# Сначала зависимости — слой кэшируется, пока package*.json не менялись.
COPY package.json package-lock.json* ./
RUN npm install

# Исходники (bind-mount ./frontend перекрывает этот слой при docker compose up).
COPY . .

EXPOSE 5173

# Хост и порт продублированы, чтобы образ работал и без compose-команды.
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
