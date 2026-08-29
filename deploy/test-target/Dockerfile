FROM node:22-alpine

ENV NODE_ENV=production
WORKDIR /app
COPY --chown=node:node deploy/test-target/package.json deploy/test-target/server.mjs ./

USER node
CMD ["node", "server.mjs"]
