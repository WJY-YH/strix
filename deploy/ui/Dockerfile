FROM node:22-alpine

ENV NODE_ENV=production
WORKDIR /app
COPY --chown=node:node deploy/ui/ ./

USER node
CMD ["node", "server.mjs"]
