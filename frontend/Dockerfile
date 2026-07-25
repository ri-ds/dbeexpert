# Stage 1: build the static bundle.
FROM node:20-alpine AS build

WORKDIR /app

# Copy manifests first so dependency installs stay cached across source edits.
COPY package.json ./
COPY package-lock.json* ./

# Use the lockfile when it exists, otherwise fall back to a plain install.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY . .

RUN npm run build

# Stage 2: serve the bundle with nginx.
FROM nginx:alpine AS serve

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
