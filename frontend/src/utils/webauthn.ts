export function isWebAuthnSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.PublicKeyCredential === "function" &&
    typeof navigator !== "undefined" &&
    Boolean(navigator.credentials) &&
    typeof navigator.credentials.create === "function" &&
    typeof navigator.credentials.get === "function"
  );
}

export function isPasskeyOriginUsable(): { ok: boolean; reason: string } {
  if (!isWebAuthnSupported()) {
    return {
      ok: false,
      reason: "当前浏览器不支持 WebAuthn / Passkey（请使用现代浏览器最新版本）",
    };
  }
  if (typeof window !== "undefined") {
    const host = window.location.hostname || "";
    const isLocalhost = host === "localhost" || host.endsWith(".localhost");
    const isIp =
      /^\d{1,3}(?:\.\d{1,3}){3}$/.test(host) || host.includes(":");
    if (!window.isSecureContext && !isLocalhost) {
      return {
        ok: false,
        reason: "Passkey 需要通过 HTTPS 加密域名或 http://localhost 访问，当前非安全上下文。",
      };
    }
    if (isIp && !isLocalhost) {
      return {
        ok: false,
        reason: `当前通过裸 IP (${host}) 访问，WebAuthn 不允许 IP 作为域名；请改用绑定域名（HTTPS）或 localhost 访问。`,
      };
    }
  }
  return { ok: true, reason: "" };
}

function base64UrlToBuffer(value: string): ArrayBuffer {
  const padded = value.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((value.length + 3) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes.buffer;
}

function bufferToBase64Url(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.byteLength; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function mapDescriptors(list: unknown): PublicKeyCredentialDescriptor[] | undefined {
  if (!Array.isArray(list)) {
    return undefined;
  }
  return list
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === "object"))
    .map((item) => ({
      type: "public-key" as const,
      id: base64UrlToBuffer(String(item.id || "")),
      transports: Array.isArray(item.transports)
        ? (item.transports.filter((t): t is AuthenticatorTransport => typeof t === "string"))
        : undefined,
    }));
}

export async function createPasskeyCredential(
  rawOptions: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const userObj = (rawOptions.user || {}) as Record<string, unknown>;
  const publicKey: PublicKeyCredentialCreationOptions = {
    ...(rawOptions as unknown as PublicKeyCredentialCreationOptions),
    challenge: base64UrlToBuffer(String(rawOptions.challenge || "")),
    user: {
      ...(userObj as unknown as PublicKeyCredentialUserEntity),
      id: base64UrlToBuffer(String(userObj.id || "")),
    },
    excludeCredentials: mapDescriptors(rawOptions.excludeCredentials),
  };

  const cred = await navigator.credentials.create({ publicKey });
  if (!(cred instanceof PublicKeyCredential)) {
    throw new Error("未获取到有效的 Passkey 注册凭据");
  }
  const response = cred.response as AuthenticatorAttestationResponse;
  const transports =
    typeof response.getTransports === "function" ? response.getTransports() : [];

  return {
    id: cred.id,
    rawId: bufferToBase64Url(cred.rawId),
    type: cred.type,
    authenticatorAttachment: cred.authenticatorAttachment || undefined,
    response: {
      clientDataJSON: bufferToBase64Url(response.clientDataJSON),
      attestationObject: bufferToBase64Url(response.attestationObject),
      transports,
    },
    clientExtensionResults:
      typeof cred.getClientExtensionResults === "function"
        ? cred.getClientExtensionResults()
        : {},
  };
}

export async function getPasskeyAssertion(
  rawOptions: Record<string, unknown>
): Promise<Record<string, unknown>> {
  const publicKey: PublicKeyCredentialRequestOptions = {
    ...(rawOptions as unknown as PublicKeyCredentialRequestOptions),
    challenge: base64UrlToBuffer(String(rawOptions.challenge || "")),
    allowCredentials: mapDescriptors(rawOptions.allowCredentials),
  };

  const cred = await navigator.credentials.get({ publicKey });
  if (!(cred instanceof PublicKeyCredential)) {
    throw new Error("未获取到有效的 Passkey 登录签名");
  }
  const response = cred.response as AuthenticatorAssertionResponse;

  return {
    id: cred.id,
    rawId: bufferToBase64Url(cred.rawId),
    type: cred.type,
    authenticatorAttachment: cred.authenticatorAttachment || undefined,
    response: {
      clientDataJSON: bufferToBase64Url(response.clientDataJSON),
      authenticatorData: bufferToBase64Url(response.authenticatorData),
      signature: bufferToBase64Url(response.signature),
      userHandle: response.userHandle ? bufferToBase64Url(response.userHandle) : null,
    },
    clientExtensionResults:
      typeof cred.getClientExtensionResults === "function"
        ? cred.getClientExtensionResults()
        : {},
  };
}
