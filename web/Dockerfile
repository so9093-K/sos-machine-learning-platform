# 이 이미지는 로컬 Compose의 Vite 개발 서버용이며 공개용 Web 배포 이미지가 아니다.
FROM node:24-bookworm-slim
WORKDIR /workspace/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit
COPY web/ ./
RUN chown -R node:node /workspace/web
USER node
EXPOSE 9601
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
