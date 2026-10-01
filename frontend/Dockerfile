FROM node:24.14.0-alpine

WORKDIR /app

COPY package*.json ./
RUN npm ci

COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src

EXPOSE 5174

CMD ["npm", "run", "dev"]
