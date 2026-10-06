# -----------------------------------------------------------------------------
# Multi-stage Dockerfile for .NET Framework 4.8 Media Worker (Windows Container)
# NOTE: Requires Windows Container mode in Docker Desktop / Windows Server
# -----------------------------------------------------------------------------

# Step 1: Build stage
FROM mcr.microsoft.com/dotnet/framework/sdk:4.8-windowsservercore-ltsc2022 AS build
WORKDIR /src

# Copy project file and restore dependencies via nuget/msbuild
COPY MediaWorker.csproj ./
RUN nuget restore MediaWorker.csproj

# Copy source code and build Release output
COPY . .
RUN msbuild MediaWorker.csproj /p:Configuration=Release /p:Platform=x64 /p:OutputPath=C:\app\publish

# -----------------------------------------------------------------------------
# Step 2: Runtime stage
# -----------------------------------------------------------------------------
FROM mcr.microsoft.com/dotnet/framework/runtime:4.8-windowsservercore-ltsc2022 AS runtime
WORKDIR /app

# Copy built application from build stage
COPY --from=build C:/app/publish .

# Expose Graph Communications Call Signaling port
EXPOSE 8445

# Set entrypoint to run MediaWorker
ENTRYPOINT ["MediaWorker.exe"]
