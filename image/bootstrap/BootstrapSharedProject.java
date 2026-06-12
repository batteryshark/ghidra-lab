// Ghidra Lab: create a local shared project bound to a Ghidra Server repository.
//
// The upstream GhidraMCP REST API can open/checkout/checkin a server-bound
// project but cannot CREATE one (see bethington/ghidra-mcp discussion #119).
// This standalone main fills exactly that gap and nothing more: connect to the
// server as the agent account, create the repository if missing, optionally
// grant human users access, then create the shared project on disk. It is run
// once per repository at container start, gated by a .gpr marker file.
//
// Config comes from the environment so the entrypoint stays declarative:
//   GHIDRA_SERVER_HOST, GHIDRA_SERVER_PORT, GHIDRA_SERVER_USER,
//   GHIDRA_SERVER_PASSWORD, GHIDRA_LAB_DEFAULT_REPOSITORY,
//   GHIDRA_LAB_SHARED_PROJECT_ROOT, GHIDRA_LAB_REPO_USERS (semicolon list).

import java.io.File;
import java.util.ArrayList;
import java.util.List;

import ghidra.GhidraApplicationLayout;
import ghidra.base.project.GhidraProject;
import ghidra.framework.Application;
import ghidra.framework.HeadlessGhidraApplicationConfiguration;
import ghidra.framework.client.ClientUtil;
import ghidra.framework.client.PasswordClientAuthenticator;
import ghidra.framework.client.RepositoryAdapter;
import ghidra.framework.model.Project;
import ghidra.framework.model.ProjectLocator;
import ghidra.framework.project.DefaultProjectManager;
import ghidra.framework.remote.User;

public final class BootstrapSharedProject {

    // DefaultProjectManager's constructor is protected; a trivial subclass
    // exposes createProject(locator, repository, remember) to us.
    private static final class LabProjectManager extends DefaultProjectManager {
        LabProjectManager() {
            super();
        }
    }

    public static void main(String[] args) {
        try {
            run();
            System.out.println("bootstrap: OK");
            System.exit(0);
        }
        catch (Exception e) {
            System.err.println("bootstrap: FAILED: " + e.getMessage());
            e.printStackTrace();
            System.exit(1);
        }
    }

    private static void run() throws Exception {
        String host = env("GHIDRA_SERVER_HOST", "");
        int port = intEnv("GHIDRA_SERVER_PORT", 13100);
        String user = env("GHIDRA_SERVER_USER", "");
        String password = env("GHIDRA_SERVER_PASSWORD", "");
        String repoName = env("GHIDRA_LAB_DEFAULT_REPOSITORY", "GhidraLab");
        String projectRoot = env("GHIDRA_LAB_SHARED_PROJECT_ROOT", "/data/projects");
        String projectName = repoName + "_agent";

        if (host.isEmpty() || user.isEmpty() || password.isEmpty()) {
            throw new IllegalStateException(
                "GHIDRA_SERVER_HOST, GHIDRA_SERVER_USER and GHIDRA_SERVER_PASSWORD are required");
        }

        // Headless Ghidra runtime, the same initialization analyzeHeadless does.
        if (!Application.isInitialized()) {
            Application.initializeApplication(
                new GhidraApplicationLayout(), new HeadlessGhidraApplicationConfiguration());
        }

        // Non-interactive credentials for the agent service account.
        ClientUtil.setClientAuthenticator(new PasswordClientAuthenticator(user, password));

        System.out.println("bootstrap: connecting to ghidra://" + host + ":" + port + "/" + repoName
            + " as " + user);
        RepositoryAdapter repository = GhidraProject.getServerRepository(host, port, repoName, true);
        if (repository == null) {
            throw new IllegalStateException("could not obtain repository handle for " + repoName);
        }
        if (!repository.isConnected()) {
            repository.connect();
        }
        System.out.println("bootstrap: repository connected=" + repository.isConnected());

        grantRepositoryUsers(repository, user, env("GHIDRA_LAB_REPO_USERS", ""));

        File parent = new File(projectRoot);
        if (!parent.isDirectory() && !parent.mkdirs()) {
            throw new IllegalStateException("could not create project root: " + projectRoot);
        }
        ProjectLocator locator = new ProjectLocator(parent.getAbsolutePath(), projectName);
        if (locator.exists()) {
            System.out.println("bootstrap: shared project already exists at " + locator.getMarkerFile());
            return;
        }

        Project project = new LabProjectManager().createProject(locator, repository, false);
        if (project == null) {
            throw new IllegalStateException("createProject returned null for " + projectName);
        }
        project.close();
        System.out.println("bootstrap: created shared project " + projectName + " at "
            + locator.getMarkerFile());
    }

    // Best-effort ACL grant. The repo's creator (agent) is already its admin;
    // this opens it to the human GUI users. Failure is logged, not fatal: the
    // user list can also be managed server-side with svrAdmin -grant.
    private static void grantRepositoryUsers(RepositoryAdapter repository, String agentUser, String extra) {
        List<User> users = new ArrayList<>();
        addUser(users, agentUser);
        for (String name : extra.split("[;,\\s]+")) {
            if (!name.trim().isEmpty()) {
                addUser(users, name.trim());
            }
        }
        if (users.isEmpty()) {
            return;
        }
        try {
            repository.setUserList(users.toArray(new User[0]), false);
            System.out.println("bootstrap: repository ACL set for " + users.size() + " user(s)");
        }
        catch (Exception e) {
            System.err.println("bootstrap: WARNING could not set repository ACL (" + e.getMessage()
                + "); grant manually with svrAdmin -grant if a human user needs access");
        }
    }

    private static void addUser(List<User> users, String name) {
        for (User existing : users) {
            if (existing.getName().equals(name)) {
                return;
            }
        }
        users.add(new User(name, User.ADMIN));
    }

    private static String env(String name, String fallback) {
        String value = System.getenv(name);
        return (value == null || value.trim().isEmpty()) ? fallback : value.trim();
    }

    private static int intEnv(String name, int fallback) {
        try {
            String value = System.getenv(name);
            return (value == null || value.trim().isEmpty()) ? fallback : Integer.parseInt(value.trim());
        }
        catch (NumberFormatException e) {
            return fallback;
        }
    }
}
