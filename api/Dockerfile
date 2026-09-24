FROM node:18-alpine
WORKDIR /usr/src/app
RUN apk add --no-cache python3 make g++
COPY package.json ./
RUN npm install
COPY . .
EXPOSE 4000
CMD ["node","app.js"]