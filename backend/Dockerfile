FROM eclipse-temurin:25-jdk AS build
WORKDIR /src
COPY . .
WORKDIR /src/backend
RUN ./gradlew bootJar --no-daemon -x test

FROM eclipse-temurin:25-jre
WORKDIR /app
COPY --from=build /src/backend/build/libs/*.jar app.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "app.jar"]
