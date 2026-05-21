FROM node:22-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY index.html tsconfig.json vite.config.ts ./
COPY src ./src

EXPOSE 5173

CMD ["npm", "run", "dev"]
