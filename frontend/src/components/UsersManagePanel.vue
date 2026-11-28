<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import {
  Ban,
  Check,
  CheckCircle2,
  Copy,
  Edit3,
  Link2,
  Plus,
  ShieldOff,
  Ticket,
  Trash2,
  X,
} from "lucide-vue-next";
import { apiClient } from "../api/client";
import {
  appStore,
  formatUtcTime,
  isAdminUser,
  notify,
  refreshBootstrapState,
} from "../composables/useAppStore";
import type { AuthUser, InviteCodeItem, RegistrationMode, UserRole } from "../types/api";

const props = defineProps<{
  autoOpenCreateSignal?: number;
}>();

const emit = defineEmits<{
  (e: "consumeCreateSignal"): void;
}>();

const REGISTRATION_OPTIONS: { value: RegistrationMode; title: string; desc: string }[] = [
  {
    value: "closed",
    title: "关闭注册（默认）",
    desc: "仅管理员可在后台手动新建账号，登录页不展示自助注册入口。",
  },
  {
    value: "invite_only",
    title: "仅限邀请制注册",
    desc: "新用户须凭管理员发放的有效邀请码完成注册，并按邀请码分配角色。",
  },
  {
    value: "open",
    title: "开放注册",
    desc: "允许访客在登录页自由注册普通用户账号（无需邀请码）。",
  },
];

const loading = ref(false);
const saving = ref(false);
const users = ref<AuthUser[]>([]);
const modalOpen = ref(false);
const editingUser = ref<AuthUser | null>(null);

const savingRegMode = ref(false);
const invitesLoading = ref(false);
const creatingInvite = ref(false);
const invites = ref<InviteCodeItem[]>([]);

const currentRegMode = computed<RegistrationMode>(
  () => appStore.authStatus.registration_mode || "closed"
);

const form = reactive({
  username: "",
  display_name: "",
  email: "",
  role: "user" as UserRole,
  is_active: true,
  password: "",
});

const inviteForm = reactive({
  role: "user" as UserRole,
  max_uses: 1,
  expires_in_hours: "168",
  note: "",
});

async function loadUsers(): Promise<void> {
  if (!isAdminUser()) {
    return;
  }
  loading.value = true;
  try {
    const resp = await apiClient.listUsers();
    users.value = resp.items;
  } catch (err) {
    notify(err instanceof Error ? err.message : "加载用户列表失败", "error");
  } finally {
    loading.value = false;
  }
}

async function loadInvites(): Promise<void> {
  if (!isAdminUser()) {
    return;
  }
  invitesLoading.value = true;
  try {
    const resp = await apiClient.listInvites();
    invites.value = resp.items;
  } catch (err) {
    notify(err instanceof Error ? err.message : "加载邀请码列表失败", "error");
  } finally {
    invitesLoading.value = false;
  }
}

async function changeRegistrationMode(mode: RegistrationMode): Promise<void> {
  if (mode === currentRegMode.value || savingRegMode.value) {
    return;
  }
  savingRegMode.value = true;
  try {
    await apiClient.updateSettings({ registration_mode: mode });
    await refreshBootstrapState();
    const matched = REGISTRATION_OPTIONS.find((opt) => opt.value === mode);
    notify(`已切换用户注册策略：${matched?.title || mode}`, "success");
  } catch (err) {
    notify(err instanceof Error ? err.message : "更新注册策略失败", "error");
  } finally {
    savingRegMode.value = false;
  }
}

async function handleCreateInvite(): Promise<void> {
  creatingInvite.value = true;
  try {
    const parsedMaxUses = Math.max(1, Math.min(1000, Number(inviteForm.max_uses) || 1));
    const parsedHours = inviteForm.expires_in_hours ? Number(inviteForm.expires_in_hours) : null;
    const created = await apiClient.createInvite({
      role: inviteForm.role,
      max_uses: parsedMaxUses,
      expires_in_hours: parsedHours && parsedHours > 0 ? parsedHours : null,
      note: inviteForm.note.trim(),
    });
    inviteForm.note = "";
    await loadInvites();
    notify(`已生成邀请码：${created.code}`, "success");
  } catch (err) {
    notify(err instanceof Error ? err.message : "生成邀请码失败", "error");
  } finally {
    creatingInvite.value = false;
  }
}

async function handleToggleInvite(inv: InviteCodeItem): Promise<void> {
  try {
    await apiClient.toggleInvite(inv.id, !inv.is_active);
    notify(inv.is_active ? `已停用邀请码 ${inv.code}` : `已重新启用邀请码 ${inv.code}`, "success");
    await loadInvites();
  } catch (err) {
    notify(err instanceof Error ? err.message : "更新邀请码状态失败", "error");
  }
}

async function handleDeleteInvite(inv: InviteCodeItem): Promise<void> {
  if (!window.confirm(`确定删除邀请码「${inv.code}」吗？`)) {
    return;
  }
  try {
    await apiClient.deleteInvite(inv.id);
    notify(`已删除邀请码 ${inv.code}`, "success");
    await loadInvites();
  } catch (err) {
    notify(err instanceof Error ? err.message : "删除邀请码失败", "error");
  }
}

function inviteStatusInfo(inv: InviteCodeItem): { label: string; cls: string } {
  if (!inv.is_active) {
    return { label: "已停用", cls: "bad" };
  }
  if (inv.expires_at && new Date(inv.expires_at).getTime() <= Date.now()) {
    return { label: "已过期", cls: "bad" };
  }
  if (inv.used_count >= inv.max_uses) {
    return { label: "已用尽", cls: "neutral" };
  }
  return { label: "可用", cls: "good" };
}

async function copyToClipboard(text: string, msg: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    notify(msg, "success");
  } catch {
    notify(`内容：${text}`, "info");
  }
}

function copyInviteLink(code: string): void {
  const url = `${window.location.origin}/?invite=${encodeURIComponent(code)}`;
  void copyToClipboard(url, "已复制邀请注册直达链接");
}

function openCreateModal(): void {
  editingUser.value = null;
  form.username = "";
  form.display_name = "";
  form.email = "";
  form.role = "user";
  form.is_active = true;
  form.password = "";
  modalOpen.value = true;
}

function openEditModal(user: AuthUser): void {
  editingUser.value = user;
  form.username = user.username;
  form.display_name = user.display_name;
  form.email = user.email;
  form.role = user.role;
  form.is_active = user.is_active;
  form.password = "";
  modalOpen.value = true;
}

async function saveUser(): Promise<void> {
  saving.value = true;
  try {
    if (editingUser.value) {
      await apiClient.updateUser(editingUser.value.id, {
        display_name: form.display_name.trim(),
        email: form.email.trim(),
        role: form.role,
        is_active: form.is_active,
        ...(form.password ? { new_password: form.password } : {}),
      });
      notify(`已更新用户「${editingUser.value.username}」`, "success");
    } else {
      await apiClient.createUser({
        username: form.username.trim(),
        password: form.password,
        display_name: form.display_name.trim(),
        email: form.email.trim(),
        role: form.role,
        is_active: form.is_active,
      });
      notify(`已创建用户「${form.username.trim()}」`, "success");
    }
    modalOpen.value = false;
    await loadUsers();
  } catch (err) {
    notify(err instanceof Error ? err.message : "保存用户失败", "error");
  } finally {
    saving.value = false;
  }
}

async function handleResetOtp(user: AuthUser): Promise<void> {
  if (!window.confirm(`确定清除用户「${user.username}」绑定的 OTP 二步验证设备吗？`)) {
    return;
  }
  try {
    await apiClient.resetUserOtp(user.id);
    notify(`已清除用户「${user.username}」的 OTP 绑定`, "success");
    await loadUsers();
  } catch (err) {
    notify(err instanceof Error ? err.message : "重置 OTP 失败", "error");
  }
}

async function handleDeleteUser(user: AuthUser): Promise<void> {
  if (!window.confirm(`确定永久删除用户「${user.username}」吗？此操作不可撤销。`)) {
    return;
  }
  try {
    await apiClient.deleteUser(user.id);
    notify(`已删除用户「${user.username}」`, "success");
    await loadUsers();
  } catch (err) {
    notify(err instanceof Error ? err.message : "删除用户失败", "error");
  }
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape" && modalOpen.value) {
    modalOpen.value = false;
  }
}

watch(
  () => props.autoOpenCreateSignal,
  (val) => {
    if (val && val > 0) {
      openCreateModal();
      emit("consumeCreateSignal");
    }
  }
);

onMounted(() => {
  window.addEventListener("keydown", onKeydown);
  if (props.autoOpenCreateSignal && props.autoOpenCreateSignal > 0) {
    openCreateModal();
    emit("consumeCreateSignal");
  }
  void Promise.all([loadUsers(), loadInvites()]);
});

onUnmounted(() => {
  window.removeEventListener("keydown", onKeydown);
});
</script>

<template>
  <section v-if="isAdminUser()" class="settings-section">
    <div class="panel">
      <div class="panel-header">
        <div>
          <h2 class="panel-title">用户与权限管理</h2>
          <p class="panel-sub">
            管理本地用户、新用户自助注册模式及邀请码，支持分配管理员/普通用户角色、禁用或重置 OTP。
          </p>
        </div>
        <button type="button" class="navbtn primary" @click="openCreateModal">
          <Plus :size="14" aria-hidden="true" />
          新建用户
        </button>
      </div>
    </div>

    <!-- 用户自助注册策略控制面板 -->
    <div class="panel stack-md">
      <div class="row-between">
        <div>
          <h3 class="section-subhead">本地账号注册模式</h3>
          <p class="panel-sub">
            控制登录页是否允许新用户自行创建本地账号；OIDC 自动建号由「OIDC」独立管理。
          </p>
        </div>
        <span class="pill info">
          <Check :size="12" aria-hidden="true" />
          当前：{{ REGISTRATION_OPTIONS.find((o) => o.value === currentRegMode)?.title }}
        </span>
      </div>

      <div class="reg-mode-grid" role="group" aria-label="本地账号注册模式">
        <button
          v-for="opt in REGISTRATION_OPTIONS"
          :key="opt.value"
          type="button"
          class="reg-mode-option"
          :aria-pressed="currentRegMode === opt.value"
          :disabled="savingRegMode"
          @click="changeRegistrationMode(opt.value)"
        >
          <strong>{{ opt.title }}</strong>
          <span>{{ opt.desc }}</span>
        </button>
      </div>
    </div>

    <!-- 邀请码生成与管理面板 -->
    <div class="panel stack-md">
      <div class="row-between">
        <div>
          <h3 class="section-subhead">邀请码管理</h3>
          <p class="panel-sub">
            <template v-if="currentRegMode === 'invite_only'">
              已启用「仅限邀请制注册」：发放下方邀请码或直达链接后，新用户可自助完成注册。
            </template>
            <template v-else>
              提前创建或管理邀请码；切换上方策略为「仅限邀请制注册」后即可生效。
            </template>
          </p>
        </div>
        <span class="pill neutral">共 {{ invites.length }} 个邀请码</span>
      </div>

      <form class="sub-box form-stack" @submit.prevent="handleCreateInvite">
        <div class="form-grid-2">
          <label class="field-label">
            受邀账号角色
            <select v-model="inviteForm.role" name="invite_role" class="select">
              <option value="user">普通用户（推荐）</option>
              <option value="admin">管理员</option>
            </select>
          </label>
          <label class="field-label">
            最大使用次数
            <input
              v-model.number="inviteForm.max_uses"
              name="invite_max_uses"
              type="number"
              min="1"
              max="1000"
              required
              class="input"
            />
          </label>
        </div>
        <div class="form-grid-2">
          <label class="field-label">
            有效期
            <select v-model="inviteForm.expires_in_hours" name="invite_expires" class="select">
              <option value="24">24 小时</option>
              <option value="168">7 天</option>
              <option value="720">30 天</option>
              <option value="">永不过期</option>
            </select>
          </label>
          <label class="field-label">
            备注说明（可选）
            <input
              v-model="inviteForm.note"
              name="invite_note"
              maxlength="120"
              placeholder="例如：舰队成员群发放"
              class="input"
            />
          </label>
        </div>
        <div class="row-end">
          <button type="submit" class="navbtn primary" :disabled="creatingInvite">
            <Ticket :size="14" aria-hidden="true" />
            {{ creatingInvite ? "生成中…" : "生成邀请码" }}
          </button>
        </div>
      </form>

      <div v-if="invitesLoading && invites.length === 0" class="muted cell-meta">
        正在加载邀请码列表…
      </div>
      <p v-else-if="invites.length === 0" class="muted cell-meta" style="margin: 0">
        暂无邀请码记录，可在上方按需生成。
      </p>
      <div v-else class="table-card">
        <table class="data-table">
          <thead>
            <tr>
              <th>邀请码</th>
              <th>角色</th>
              <th>使用进度</th>
              <th>状态</th>
              <th>过期时间</th>
              <th>备注</th>
              <th class="align-right">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="inv in invites" :key="inv.id">
              <td>
                <code class="invite-code-chip">{{ inv.code }}</code>
              </td>
              <td>
                <span class="pill" :class="inv.role === 'admin' ? 'warn' : 'neutral'">
                  {{ inv.role === "admin" ? "管理员" : "普通用户" }}
                </span>
              </td>
              <td class="mono cell-nowrap">{{ inv.used_count }} / {{ inv.max_uses }}</td>
              <td>
                <span class="pill" :class="inviteStatusInfo(inv).cls">
                  {{ inviteStatusInfo(inv).label }}
                </span>
              </td>
              <td class="muted cell-nowrap">
                {{ inv.expires_at ? formatUtcTime(inv.expires_at) : "长期有效" }}
              </td>
              <td class="muted cell-meta">{{ inv.note || "-" }}</td>
              <td class="align-right">
                <div class="inline-actions">
                  <button
                    type="button"
                    class="navbtn btn-sm"
                    title="复制邀请码"
                    @click="copyToClipboard(inv.code, `已复制邀请码 ${inv.code}`)"
                  >
                    <Copy :size="12" aria-hidden="true" />
                    复制码
                  </button>
                  <button
                    type="button"
                    class="navbtn btn-sm"
                    title="复制带邀请码的注册链接"
                    @click="copyInviteLink(inv.code)"
                  >
                    <Link2 :size="12" aria-hidden="true" />
                    复制链接
                  </button>
                  <button
                    type="button"
                    class="navbtn btn-sm"
                    @click="handleToggleInvite(inv)"
                  >
                    <Ban v-if="inv.is_active" :size="12" aria-hidden="true" />
                    <CheckCircle2 v-else :size="12" aria-hidden="true" />
                    {{ inv.is_active ? "停用" : "启用" }}
                  </button>
                  <button
                    type="button"
                    class="navbtn danger btn-sm"
                    @click="handleDeleteInvite(inv)"
                  >
                    <Trash2 :size="12" aria-hidden="true" />
                    删除
                  </button>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="loading && users.length === 0" class="panel muted panel-loading">
      正在加载用户列表…
    </div>

    <div v-else class="table-card">
      <table class="data-table">
        <thead>
          <tr>
            <th>用户名 / 显示名</th>
            <th>邮箱</th>
            <th>角色</th>
            <th>状态</th>
            <th>认证方式</th>
            <th>最近登录</th>
            <th class="align-right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="user in users" :key="user.id">
            <td>
              <strong>{{ user.username }}</strong>
              <span v-if="user.id === appStore.authStatus.user?.id" class="pill info pill-inline">
                当前登录
              </span>
              <div v-if="user.display_name && user.display_name !== user.username" class="muted cell-sub">
                {{ user.display_name }}
              </div>
            </td>
            <td class="muted cell-meta">{{ user.email || "-" }}</td>
            <td>
              <span class="pill" :class="user.role === 'admin' ? 'warn' : 'neutral'">
                {{ user.role === "admin" ? "管理员" : "普通用户" }}
              </span>
            </td>
            <td>
              <span class="pill" :class="user.is_active ? 'good' : 'bad'">
                {{ user.is_active ? "启用中" : "已禁用" }}
              </span>
            </td>
            <td>
              <div class="badge-group">
                <span v-if="user.has_password" class="pill neutral">密码</span>
                <span v-if="user.totp_enabled" class="pill good">OTP</span>
                <span v-if="user.passkey_count > 0" class="pill good">Passkey ×{{ user.passkey_count }}</span>
                <span v-if="user.oidc_bound" class="pill info">OIDC</span>
              </div>
            </td>
            <td class="muted cell-nowrap">
              {{ user.last_login_at ? formatUtcTime(user.last_login_at) : "从未登录" }}
            </td>
            <td class="align-right">
              <div class="inline-actions">
                <button
                  type="button"
                  class="navbtn btn-sm"
                  @click="openEditModal(user)"
                >
                  <Edit3 :size="12" aria-hidden="true" />
                  编辑
                </button>
                <button
                  v-if="user.totp_enabled"
                  type="button"
                  class="navbtn btn-sm"
                  @click="handleResetOtp(user)"
                >
                  <ShieldOff :size="12" aria-hidden="true" />
                  清 OTP
                </button>
                <button
                  v-if="user.id !== appStore.authStatus.user?.id"
                  type="button"
                  class="navbtn danger btn-sm"
                  @click="handleDeleteUser(user)"
                >
                  <Trash2 :size="12" aria-hidden="true" />
                  删除
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 新建/编辑用户弹窗 -->
    <Transition name="holo-modal">
      <div v-if="modalOpen" class="modal-backdrop" @click.self="modalOpen = false">
      <div
        class="modal-card compact"
        role="dialog"
        aria-modal="true"
        aria-labelledby="user-modal-title"
      >
        <div class="modal-head">
          <h3 id="user-modal-title">
            {{ editingUser ? `编辑用户 · ${editingUser.username}` : "新建本地用户" }}
          </h3>
          <button
            type="button"
            class="navbtn btn-icon"
            aria-label="关闭弹窗"
            @click="modalOpen = false"
          >
            <X :size="15" aria-hidden="true" />
          </button>
        </div>

        <form class="form-stack" @submit.prevent="saveUser">
          <label v-if="!editingUser" class="field-label">
            登录用户名
            <input
              v-model="form.username"
              name="username"
              required
              minlength="2"
              autocomplete="username"
              spellcheck="false"
              placeholder="例如 pilot_user"
              class="input"
            />
          </label>

          <div class="form-grid-2">
            <label class="field-label">
              显示昵称
              <input v-model="form.display_name" name="display_name" class="input" />
            </label>
            <label class="field-label">
              电子邮箱
              <input
                v-model="form.email"
                name="email"
                type="email"
                autocomplete="email"
                spellcheck="false"
                placeholder="name@example.com"
                class="input"
              />
            </label>
          </div>

          <div class="form-grid-2">
            <label class="field-label">
              账号角色
              <select v-model="form.role" name="role" class="select">
                <option value="user">普通用户（可看商店与规划路线）</option>
                <option value="admin">管理员（全量配置与推送权）</option>
              </select>
            </label>
            <label class="field-label">
              账号状态
              <select v-model="form.is_active" name="is_active" class="select">
                <option :value="true">正常启用</option>
                <option :value="false">停用（禁止登录）</option>
              </select>
            </label>
          </div>

          <label class="field-label">
            {{ editingUser ? "重置密码（不改请留空，至少 8 位）" : "初始密码（至少 8 位）" }}
            <input
              v-model="form.password"
              name="password"
              type="password"
              :required="!editingUser"
              minlength="8"
              autocomplete="new-password"
              class="input"
            />
          </label>

          <div class="modal-foot">
            <button type="button" class="navbtn" @click="modalOpen = false">取消</button>
            <button type="submit" class="navbtn primary" :disabled="saving">
              {{ saving ? "保存中…" : "保存" }}
            </button>
          </div>
        </form>
      </div>
    </div>
    </Transition>
  </section>
</template>
