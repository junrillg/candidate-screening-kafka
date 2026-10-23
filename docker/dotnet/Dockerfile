FROM mcr.microsoft.com/dotnet/sdk:10.0.402@sha256:0d99ebf1ccad15037b5c98c9fe8979d6b8dbac23812feae2277a0b277b5c5ffa AS build
ARG PROJECT
WORKDIR /src
COPY global.json Directory.Build.props .editorconfig ./
COPY services/ ./services/
RUN dotnet restore "services/${PROJECT}/${PROJECT}.csproj" --locked-mode \
    && dotnet publish "services/${PROJECT}/${PROJECT}.csproj" --no-restore -c Release -o /out

FROM mcr.microsoft.com/dotnet/aspnet:10.0.13@sha256:d5633778c754e89a386882eddf8dbee6942447a8a4b8267c19cf1e1158d0b0ac
ARG PROJECT
WORKDIR /app
COPY --from=build /out/ .
COPY docker/dotnet/certs/rds-global-bundle.pem /app/certs/rds-global-bundle.pem
RUN ln -s "${PROJECT}.dll" service.dll \
    && mkdir /keys && chown "$APP_UID:$APP_UID" /keys /tmp && chmod 700 /keys /tmp
USER $APP_UID
VOLUME ["/tmp"]
ENV ASPNETCORE_URLS=http://+:8080
EXPOSE 8080
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=6 CMD ["dotnet", "service.dll", "--healthcheck"]
ENTRYPOINT ["dotnet", "service.dll"]
