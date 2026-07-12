ARG GHIDRA_VERSION=12.1.2
ARG GHIDRA_DATE=20260605
ARG GHIDRA_SHA256=b62e81a0390618466c019c60d8c2f796ced2509c4c1aea4a37644a77272cf99d

FROM eclipse-temurin:21-jdk AS builder
ARG GHIDRA_VERSION
ARG GHIDRA_DATE
ARG GHIDRA_SHA256
ARG GHIDRA_MCP_REPO=https://github.com/bethington/ghidra-mcp.git
ARG GHIDRA_MCP_REF=378f727b7059163eb6ba3ae315c4d9233decba66

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates git maven unzip wget \
    && rm -rf /var/lib/apt/lists/*

RUN wget -q -O /tmp/ghidra.zip \
    "https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_${GHIDRA_VERSION}_build/ghidra_${GHIDRA_VERSION}_PUBLIC_${GHIDRA_DATE}.zip" \
    && echo "${GHIDRA_SHA256}  /tmp/ghidra.zip" | sha256sum --check --strict \
    && unzip -q /tmp/ghidra.zip -d /opt \
    && mv /opt/ghidra_${GHIDRA_VERSION}_PUBLIC /opt/ghidra \
    && rm /tmp/ghidra.zip

# Install the Ghidra framework jars GhidraMCP's headless build links against.
RUN mvn -q install:install-file -Dfile=/opt/ghidra/Ghidra/Framework/Generic/lib/Generic.jar \
    -DgroupId=ghidra -DartifactId=Generic -Dversion=${GHIDRA_VERSION} -Dpackaging=jar \
    && mvn -q install:install-file -Dfile=/opt/ghidra/Ghidra/Framework/SoftwareModeling/lib/SoftwareModeling.jar \
    -DgroupId=ghidra -DartifactId=SoftwareModeling -Dversion=${GHIDRA_VERSION} -Dpackaging=jar \
    && mvn -q install:install-file -Dfile=/opt/ghidra/Ghidra/Framework/Project/lib/Project.jar \
    -DgroupId=ghidra -DartifactId=Project -Dversion=${GHIDRA_VERSION} -Dpackaging=jar \
    && mvn -q install:install-file -Dfile=/opt/ghidra/Ghidra/Framework/Docking/lib/Docking.jar \
    -DgroupId=ghidra -DartifactId=Docking -Dversion=${GHIDRA_VERSION} -Dpackaging=jar \
    && mvn -q install:install-file -Dfile=/opt/ghidra/Ghidra/Framework/Utility/lib/Utility.jar \
    -DgroupId=ghidra -DartifactId=Utility -Dversion=${GHIDRA_VERSION} -Dpackaging=jar \
    && mvn -q install:install-file -Dfile=/opt/ghidra/Ghidra/Framework/Gui/lib/Gui.jar \
    -DgroupId=ghidra -DartifactId=Gui -Dversion=${GHIDRA_VERSION} -Dpackaging=jar \
    && mvn -q install:install-file -Dfile=/opt/ghidra/Ghidra/Framework/FileSystem/lib/FileSystem.jar \
    -DgroupId=ghidra -DartifactId=FileSystem -Dversion=${GHIDRA_VERSION} -Dpackaging=jar \
    && mvn -q install:install-file -Dfile=/opt/ghidra/Ghidra/Framework/Help/lib/Help.jar \
    -DgroupId=ghidra -DartifactId=Help -Dversion=${GHIDRA_VERSION} -Dpackaging=jar \
    && mvn -q install:install-file -Dfile=/opt/ghidra/Ghidra/Features/Base/lib/Base.jar \
    -DgroupId=ghidra -DartifactId=Base -Dversion=${GHIDRA_VERSION} -Dpackaging=jar \
    && mvn -q install:install-file -Dfile=/opt/ghidra/Ghidra/Features/Decompiler/lib/Decompiler.jar \
    -DgroupId=ghidra -DartifactId=Decompiler -Dversion=${GHIDRA_VERSION} -Dpackaging=jar \
    && mvn -q install:install-file -Dfile=/opt/ghidra/Ghidra/Framework/DB/lib/DB.jar \
    -DgroupId=ghidra -DartifactId=DB -Dversion=${GHIDRA_VERSION} -Dpackaging=jar \
    && mvn -q install:install-file -Dfile=/opt/ghidra/Ghidra/Framework/Emulation/lib/Emulation.jar \
    -DgroupId=ghidra -DartifactId=Emulation -Dversion=${GHIDRA_VERSION} -Dpackaging=jar \
    && mvn -q install:install-file -Dfile=/opt/ghidra/Ghidra/Debug/Debugger-api/lib/Debugger-api.jar \
    -DgroupId=ghidra -DartifactId=Debugger-api -Dversion=${GHIDRA_VERSION} -Dpackaging=jar \
    && mvn -q install:install-file -Dfile=/opt/ghidra/Ghidra/Debug/Framework-TraceModeling/lib/Framework-TraceModeling.jar \
    -DgroupId=ghidra -DartifactId=Framework-TraceModeling -Dversion=${GHIDRA_VERSION} -Dpackaging=jar \
    && mvn -q install:install-file -Dfile=/opt/ghidra/Ghidra/Debug/Debugger-rmi-trace/lib/Debugger-rmi-trace.jar \
    -DgroupId=ghidra -DartifactId=Debugger-rmi-trace -Dversion=${GHIDRA_VERSION} -Dpackaging=jar

# Build the upstream GhidraMCP headless server at a pinned ref, with one added
# service. Upstream's headless version-control endpoints leave checked-out
# programs read-only and do not actually check in (discussion #119); our service
# drives the project DomainFile API so the agent's edits become new server
# versions. It is one self-contained class plus a single registration line, both
# validated by the normal Maven compile below.
RUN git clone "${GHIDRA_MCP_REPO}" /src
WORKDIR /src
RUN git checkout "${GHIDRA_MCP_REF}"

COPY image/extensions/LabVersionControlService.java /src/src/main/java/com/xebyte/headless/LabVersionControlService.java
# -Dghidra.version pins the build to whatever Ghidra we installed above, so the
# pinned ghidra-mcp ref (pom default 12.1) compiles against patch releases too.
RUN sed -i 's|endpointHandler.getEmulationService(), managementService);|endpointHandler.getEmulationService(), managementService, new LabVersionControlService(programProvider));|' \
        src/main/java/com/xebyte/headless/GhidraMCPHeadlessServer.java \
    && grep -q "new LabVersionControlService(programProvider)" src/main/java/com/xebyte/headless/GhidraMCPHeadlessServer.java \
    && mvn -q -P headless -DskipTests -Dghidra.version=${GHIDRA_VERSION} package

# Compile our one custom piece: a standalone main that creates a server-bound
# shared project. javac against the real Ghidra jars validates the API here, at
# build time, instead of failing opaquely at runtime.
COPY image/bootstrap /tmp/bootstrap
RUN mkdir -p /opt/ghidra-lab/bootstrap \
    && CP="$(find /opt/ghidra -name '*.jar' | paste -sd: -)" \
    && javac -cp "${CP}" -d /opt/ghidra-lab/bootstrap /tmp/bootstrap/*.java

FROM eclipse-temurin:21-jdk

RUN apt-get update \
    && apt-get install -y --no-install-recommends bash ca-certificates curl jq python3 python3-pip python3-venv \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/ghidra /opt/ghidra
COPY --from=builder /src /opt/ghidra-mcp
COPY --from=builder /src/target/GhidraMCP-*.jar /app/GhidraMCP.jar
COPY --from=builder /opt/ghidra-lab/bootstrap /opt/ghidra-lab/bootstrap

RUN python3 -m venv /opt/ghidra-lab/venv
COPY facade/requirements.txt /opt/ghidra-lab/requirements.txt
RUN /opt/ghidra-lab/venv/bin/pip install --upgrade pip \
    && /opt/ghidra-lab/venv/bin/pip install -r /opt/ghidra-mcp/requirements.txt \
    && /opt/ghidra-lab/venv/bin/pip install -r /opt/ghidra-lab/requirements.txt

COPY facade/ghidra_lab_mcp /opt/ghidra-lab/ghidra_lab_mcp
COPY image/bin /opt/ghidra-lab/bin
RUN chmod +x /opt/ghidra-lab/bin/*.sh

ENV PATH="/opt/ghidra-lab/venv/bin:${PATH}" \
    PYTHONPATH="/opt/ghidra-lab" \
    GHIDRA_INSTALL_DIR="/opt/ghidra" \
    GHIDRA_HOME="/opt/ghidra" \
    GHIDRA_MCP_FILE_ROOT="/data/uploads"

EXPOSE 8080
ENTRYPOINT ["/opt/ghidra-lab/bin/ghidra-lab-entrypoint.sh"]
