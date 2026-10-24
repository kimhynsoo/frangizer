# 프랜자이저 모바일 시연

기존 웹(`../frontend`)에서 독립 복제한 Vue 프로젝트입니다.
앱 시연 화면은 이 폴더에서 수정합니다. 기존 웹의 소스와 빌드에 영향을 주지 않습니다.
설치형 Android/iOS 패키지는 만들지 않습니다.

## 실행

먼저 저장소 루트에서 `docker compose up -d`로 기존 API 서버를 실행합니다.

```sh
cd frontend-mobile
npm ci
npm run dev
```

PC: http://localhost:5174
휴대폰: 같은 Wi-Fi에서 `http://<PC의 내부 IP>:5174`
브라우저 개발자 도구의 기기 모드에서 390 × 844 크기로도 확인할 수 있습니다.
620px 이하에서는 하단 메뉴와 카드형 목록을 표시합니다.

기본값은 실제 API 모드입니다. 개발 서버가 `/api` 요청을 로컬 Docker 웹 서버의
80 포트로 전달하므로 휴대폰에서도 API 주소를 별도로 입력할 필요가 없습니다.
합성 시연 데이터만 쓰려면 로컬 `.env.local`에 `VITE_USE_MOCK=true`를 설정하고 재시작합니다.
실제 `.env` 파일과 비밀값은 웹 프로젝트에서 복사하지 않았습니다.

마이크 입력은 브라우저 권한과 HTTPS 또는 localhost 보안 연결이 필요합니다.
휴대폰의 HTTP 내부 IP 접속에서는 텍스트 입력을 사용하거나 HTTPS를 준비합니다.

## 검증과 빌드

```sh
npm test
npm run build
```

결과물은 이 폴더의 `dist/`입니다. 기존 Docker 웹 빌드는 `frontend/`만 사용합니다.
복제 이후 웹과 앱의 변경은 자동 동기화되지 않습니다.
