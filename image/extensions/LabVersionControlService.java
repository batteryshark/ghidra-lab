package com.xebyte.headless;

import com.xebyte.core.*;
import ghidra.framework.data.CheckinHandler;
import ghidra.framework.model.DomainFile;
import ghidra.program.model.listing.Program;
import ghidra.util.Msg;
import ghidra.util.task.ConsoleTaskMonitor;
import ghidra.util.task.TaskMonitor;

import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Ghidra Lab version-control endpoints for the headless server.
 *
 * Upstream's headless /server/version_control/* operate on the RepositoryAdapter
 * directly, which leaves the open program backed by a read-only DomainFileProxy
 * (saves fail with "Location does not exist") and performs no real check-in (see
 * discussion #119). These endpoints instead drive the project's DomainFile API —
 * the same path the GUI uses — so a checked-out program is genuinely writable and
 * a check-in produces a new server version the GUI user can open.
 *
 * Registered by adding an instance to the AnnotationScanner service list in
 * GhidraMCPHeadlessServer.registerEndpoints(); no other upstream change.
 */
@McpToolGroup(value = "headless", description = "Ghidra Lab server-backed checkout/checkin (project DomainFile API)")
public class LabVersionControlService {

    private final HeadlessProgramProvider programProvider;
    private final TaskMonitor monitor = new ConsoleTaskMonitor();

    public LabVersionControlService(HeadlessProgramProvider programProvider) {
        this.programProvider = programProvider;
    }

    @McpTool(path = "/lab/checkout", method = "POST",
            description = "Check out a repository program through the open shared project so it becomes writable. "
                + "Call before /load_program_from_project when you intend to edit and check the program back in.",
            category = "headless")
    public Response labCheckout(
            @Param(value = "path", source = ParamSource.BODY,
                description = "Program path within the project, e.g. /folder/sample") String path,
            @Param(value = "exclusive", source = ParamSource.BODY, defaultValue = "true",
                description = "Acquire an exclusive checkout") boolean exclusive) {
        try {
            DomainFile file = resolveFile(path);
            if (file == null) {
                return Response.err("Project file not found: " + path
                    + " (open the shared project and confirm the path with /server/repository/files)");
            }
            if (!file.isVersioned()) {
                return Response.err("File is not version controlled: " + file.getPathname());
            }
            boolean checkedOut = file.isCheckedOut() || file.checkout(exclusive, monitor);
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("success", checkedOut);
            result.put("path", file.getPathname());
            result.put("checked_out", file.isCheckedOut());
            result.put("exclusive", file.isCheckedOutExclusive());
            result.put("can_checkin", file.canCheckin());
            result.put("version", file.getVersion());
            return Response.ok(result);
        } catch (Exception e) {
            Msg.error(this, "Lab checkout failed", e);
            return Response.err("Checkout failed: " + e.getMessage());
        }
    }

    @McpTool(path = "/lab/checkin", method = "POST",
            description = "Save the open program and check it in to the Ghidra Server, creating a new version "
                + "visible to GUI users. Use after editing a checked-out program.",
            category = "headless")
    public Response labCheckin(
            @Param(value = "path", source = ParamSource.BODY, defaultValue = "",
                description = "Program path; omit to use the current program") String path,
            @Param(value = "comment", source = ParamSource.BODY, defaultValue = "Agent analysis update",
                description = "Check-in comment") String comment,
            @Param(value = "keep_checked_out", source = ParamSource.BODY, defaultValue = "false",
                description = "Keep the file checked out after check-in") boolean keepCheckedOut,
            @Param(value = "save_before_checkin", source = ParamSource.BODY, defaultValue = "true",
                description = "Save in-memory changes before checking in") boolean saveBeforeCheckin) {
        try {
            Program program = resolveProgram(path);
            DomainFile file = program != null ? program.getDomainFile() : resolveFile(path);
            if (file == null) {
                return Response.err("Project file not found; pass path or load a project-backed program first");
            }

            if (saveBeforeCheckin && program != null && program.canSave()) {
                program.flushEvents();
                program.save(comment, monitor);
            }

            if (!file.canCheckin()) {
                Map<String, Object> noop = new LinkedHashMap<>();
                noop.put("success", true);
                noop.put("path", file.getPathname());
                noop.put("checked_in", false);
                noop.put("note", "no changes to check in");
                noop.put("version", file.getVersion());
                return Response.ok(noop);
            }

            final String checkinComment = (comment == null || comment.isEmpty())
                ? "Agent analysis update" : comment;
            final boolean keep = keepCheckedOut;
            file.checkin(new CheckinHandler() {
                @Override
                public String getComment() {
                    return checkinComment;
                }

                @Override
                public boolean keepCheckedOut() {
                    return keep;
                }

                @Override
                public boolean createKeepFile() {
                    return false;
                }
            }, monitor);

            Map<String, Object> result = new LinkedHashMap<>();
            result.put("success", true);
            result.put("path", file.getPathname());
            result.put("checked_in", true);
            result.put("checked_out", file.isCheckedOut());
            result.put("keep_checked_out", keepCheckedOut);
            result.put("version", file.getVersion());
            return Response.ok(result);
        } catch (Exception e) {
            Msg.error(this, "Lab checkin failed", e);
            return Response.err("Checkin failed: " + e.getMessage());
        }
    }

    @McpTool(path = "/lab/undo_checkout", method = "POST",
            description = "Discard local changes and release a checkout.",
            category = "headless")
    public Response labUndoCheckout(
            @Param(value = "path", source = ParamSource.BODY, defaultValue = "",
                description = "Program path; omit to use the current program") String path,
            @Param(value = "keep", source = ParamSource.BODY, defaultValue = "false",
                description = "Keep a local non-versioned copy") boolean keep) {
        try {
            DomainFile file = resolveFile(path);
            if (file == null) {
                return Response.err("Project file not found; pass path or load a project-backed program first");
            }
            if (!file.isCheckedOut()) {
                return Response.err("File is not checked out: " + file.getPathname());
            }
            file.undoCheckout(keep);
            Map<String, Object> result = new LinkedHashMap<>();
            result.put("success", true);
            result.put("path", file.getPathname());
            result.put("checked_out", file.isCheckedOut());
            result.put("kept_local_copy", keep);
            return Response.ok(result);
        } catch (Exception e) {
            Msg.error(this, "Lab undo checkout failed", e);
            return Response.err("Undo checkout failed: " + e.getMessage());
        }
    }

    private Program resolveProgram(String path) {
        String clean = path == null ? "" : path.trim();
        if (clean.isEmpty()) {
            return programProvider.getCurrentProgram();
        }
        int slash = clean.lastIndexOf('/');
        String name = slash >= 0 ? clean.substring(slash + 1) : clean;
        Program byName = programProvider.getProgram(name);
        return byName != null ? byName : programProvider.getCurrentProgram();
    }

    private DomainFile resolveFile(String path) {
        String clean = path == null ? "" : path.trim();
        if (clean.isEmpty()) {
            Program current = programProvider.getCurrentProgram();
            return current == null ? null : current.getDomainFile();
        }
        if (!programProvider.hasProject() || programProvider.getProject() == null) {
            return null;
        }
        return programProvider.getProject().getProjectData().getFile(clean);
    }
}
