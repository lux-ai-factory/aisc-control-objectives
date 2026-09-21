FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .
# Build-time config: where the service API and the catalogue live. Vite inlines
# these at build, so they must be passed as build args.
ARG VITE_CONTROL_OBJECTIVES_URL
ARG VITE_CATALOGUE_URL
ENV VITE_CONTROL_OBJECTIVES_URL=$VITE_CONTROL_OBJECTIVES_URL
ENV VITE_CATALOGUE_URL=$VITE_CATALOGUE_URL
RUN npm run build

RUN npm install -g serve

EXPOSE 3001

CMD ["serve", "-s", "build", "-l", "3001"]
