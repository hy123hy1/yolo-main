# Task Management Channel Submenu Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a task-management submenu into the channel page so users can create, configure, start, stop, and inspect AI tasks for the current channel using the new `/tasks` backend.

**Architecture:** Keep `Channels.vue` as the channel context shell, add a new `任务管理` tab, and move task-specific UI into dedicated child components plus a `tasks.ts` API module. Replace the old `AI能力` front-end entry with task management while leaving legacy backend channel-AI endpoints untouched for compatibility.

**Tech Stack:** Vue 3, TypeScript, Axios, existing channel page patterns, existing backend `/tasks` APIs.

---

### Task 1: Add failing frontend API surface for tasks

**Files:**
- Create: `frontend/src/api/tasks.ts`

- [ ] **Step 1: Write the failing test or verification target**

Define the required task API surface before implementation:
- `listTasks`
- `getTask`
- `createTask`
- `updateTask`
- `deleteTask`
- `startTask`
- `stopTask`
- `getTaskStatus`
- `addTaskAlgorithm`
- `updateTaskAlgorithm`
- `deleteTaskAlgorithm`

- [ ] **Step 2: Run a type-level or build-level failure check**

Run: `npm.cmd run build`
Expected: FAIL later when components import `@/api/tasks` before the file exists.

- [ ] **Step 3: Write minimal implementation**

Implement the task API wrappers in `frontend/src/api/tasks.ts` using the existing Axios helper and `/tasks` endpoints.

- [ ] **Step 4: Run verification**

Run: `npm.cmd run build`
Expected: no API import errors from `tasks.ts`

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/tasks.ts
git commit -m "feat: add task management api client"
```

### Task 2: Add task management UI components

**Files:**
- Create: `frontend/src/components/channels/TaskListPanel.vue`
- Create: `frontend/src/components/channels/TaskDetailPanel.vue`
- Create: `frontend/src/components/channels/TaskEditorModal.vue`
- Create: `frontend/src/components/channels/AlgorithmEditorModal.vue`

- [ ] **Step 1: Write the failing integration target**

Define the component props/events needed by the page:
- `TaskListPanel`
  - props: `tasks`, `selectedTaskId`, `loading`
  - emits: `select`, `create`, `start`, `stop`, `delete`
- `TaskDetailPanel`
  - props: `task`, `status`, `modelOptions`, `loading`
  - emits: `save`, `apply`, `stop`, `create-algorithm`, `edit-algorithm`, `delete-algorithm`

- [ ] **Step 2: Run verification to confirm missing imports fail**

Run: `npm.cmd run build`
Expected: FAIL once `Channels.vue` imports these components before they exist.

- [ ] **Step 3: Write minimal implementation**

Create the four components with:
- Chinese labels
- existing button/input visual language
- empty-state handling
- event-based communication only

- [ ] **Step 4: Run verification**

Run: `npm.cmd run build`
Expected: component imports resolve cleanly

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/channels/TaskListPanel.vue frontend/src/components/channels/TaskDetailPanel.vue frontend/src/components/channels/TaskEditorModal.vue frontend/src/components/channels/AlgorithmEditorModal.vue
git commit -m "feat: add task management channel components"
```

### Task 3: Integrate task management submenu into Channels page

**Files:**
- Modify: `frontend/src/pages/Channels.vue`

- [ ] **Step 1: Write the failing integration target**

Target behaviors:
- submenu includes `任务管理`
- old `AI能力` front-end entry no longer renders
- selecting `任务管理` shows task list + detail layout
- task list is filtered by current channel

- [ ] **Step 2: Run verification to observe failure**

Run: `npm.cmd run build`
Expected: FAIL until `Channels.vue` is updated to import and use the new task components.

- [ ] **Step 3: Write minimal implementation**

In `Channels.vue`:
- replace the `AI能力` tab with `任务管理`
- import the new task components
- load `/tasks` data for the current channel
- manage selected task state
- wire create/edit/delete/start/stop actions
- keep `预览`、`通道管理`、`电子围栏` behaviors unchanged

- [ ] **Step 4: Run verification**

Run: `npm.cmd run build`
Expected: build succeeds and no Vue compile errors remain

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Channels.vue
git commit -m "feat: replace channel ai tab with task management"
```

### Task 4: Wire task state refresh and action feedback

**Files:**
- Modify: `frontend/src/pages/Channels.vue`
- Modify: `frontend/src/components/channels/TaskDetailPanel.vue`
- Modify: `frontend/src/components/channels/TaskListPanel.vue`

- [ ] **Step 1: Write the failing interaction target**

Target behaviors:
- create task updates left list immediately
- create/edit algorithm updates right detail immediately
- start/stop task refreshes status block
- current page feedback uses the existing message style

- [ ] **Step 2: Run verification before implementation**

Run: `npm.cmd run build`
Expected: build may pass but interaction target is not yet satisfied by manual reasoning; proceed to implement.

- [ ] **Step 3: Write minimal implementation**

Add:
- reload hooks after create/update/delete actions
- selected task preservation after refresh
- Chinese success/error messages aligned with current page style

- [ ] **Step 4: Run verification**

Run: `npm.cmd run build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/Channels.vue frontend/src/components/channels/TaskDetailPanel.vue frontend/src/components/channels/TaskListPanel.vue
git commit -m "feat: refresh task state after task management actions"
```

### Task 5: Final verification

**Files:**
- No additional files

- [ ] **Step 1: Run frontend build**

Run: `npm.cmd run build`
Expected: PASS

- [ ] **Step 2: Smoke-check backend task routes remain available**

Run: `docker exec yolo-backend python -c "from src.main import app; print(sorted([route.path for route in app.routes if route.path.startswith('/tasks')]))"`
Expected: prints task routes including algorithms and regions endpoints

- [ ] **Step 3: Review spec coverage**

Confirm the implemented UI covers:
- task submenu
- left list / right detail
- create task
- create algorithm
- start/stop/apply
- runtime status

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/specs/2026-06-04-task-management-channel-submenu-design.md docs/superpowers/plans/2026-06-04-task-management-channel-submenu-implementation.md
git commit -m "docs: add task management channel submenu plan"
```
