import { useEffect, useState } from "react";
import {
  ApiError,
  createCandidate,
  listHrUsers,
  type DuplicateCandidateError,
} from "../../api";
import { Button } from "../../design-system/components/Button";
import { Field, SelectInput, TextInput } from "../../design-system/components/Field";
import { Modal } from "../../design-system/components/Modal";
import { useToast } from "../../design-system/components/ToastContext";
import {
  SOURCE_LABELS,
  type Candidate,
  type CandidateSource,
  type User,
  type UserListItem,
} from "../../types";
import { DuplicateResolveDialog } from "./DuplicateResolveDialog";

interface CandidateFormModalProps {
  open: boolean;
  user: User;
  onClose: () => void;
  onCreated: (candidate: Candidate) => void;
  /** Opens an existing matching candidate (duplicate flow). */
  onOpenCandidate: (id: string) => void;
}

interface CreatePayload {
  full_name: string;
  phone: string | null;
  email: string | null;
  source: CandidateSource;
  position: string;
  owner_user_id?: string;
}

/** Create-candidate form with the duplicate-confirmation flow (PRODUCT_SPEC §4). */
export function CandidateFormModal({
  open,
  user,
  onClose,
  onCreated,
  onOpenCandidate,
}: CandidateFormModalProps) {
  const { pushToast } = useToast();
  const [fullName, setFullName] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [source, setSource] = useState<CandidateSource>("site");
  const [position, setPosition] = useState("");
  const [ownerId, setOwnerId] = useState("");
  const [directory, setDirectory] = useState<UserListItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [duplicate, setDuplicate] = useState<{
    error: DuplicateCandidateError;
    payload: CreatePayload;
  } | null>(null);

  const canPickOwner = user.role !== "hr";

  useEffect(() => {
    if (!open || !canPickOwner) return;
    let cancelled = false;
    void listHrUsers()
      .then((page) => {
        if (!cancelled) setDirectory(page.items);
      })
      .catch(() => {
        // The picker stays empty when the directory is unavailable.
      });
    return () => {
      cancelled = true;
    };
  }, [open, canPickOwner]);

  const payloadFromForm = (): CreatePayload => ({
    full_name: fullName.trim(),
    phone: phone.trim() || null,
    email: email.trim() || null,
    source,
    position: position.trim(),
    owner_user_id: canPickOwner && ownerId ? ownerId : undefined,
  });

  const submit = async (payload: CreatePayload, confirmDuplicate: boolean) => {
    setSaving(true);
    setError(null);
    try {
      const created = await createCandidate({ ...payload, confirm_duplicate: confirmDuplicate });
      pushToast("success", `Кандидат «${created.full_name}» создан.`);
      setFullName("");
      setPhone("");
      setEmail("");
      setPosition("");
      setOwnerId("");
      setDuplicate(null);
      onCreated(created);
    } catch (caught) {
      if (caught instanceof Error && caught.name === "DuplicateCandidateError" && !confirmDuplicate) {
        setDuplicate({ error: caught as DuplicateCandidateError, payload });
      } else {
        setError(caught instanceof ApiError ? caught.message : "Не удалось создать кандидата.");
      }
    } finally {
      setSaving(false);
    }
  };

  const handleSubmit = () => {
    if (!fullName.trim()) {
      setError("ФИО обязательно.");
      return;
    }
    void submit(payloadFromForm(), false);
  };

  return (
    <>
      <Modal
        open={open}
        onClose={onClose}
        title="Новый кандидат"
        description="Все поля сохраняются на сервере; при совпадении телефона или email потребуется явное подтверждение."
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={onClose} disabled={saving}>
              Отмена
            </Button>
            <Button
              form="candidate-create-form"
              type="submit"
              loading={saving}
              disabled={saving}
            >
              Создать
            </Button>
          </>
        }
      >
        <form
          id="candidate-create-form"
          className="candidate-form"
          onSubmit={(event) => {
            event.preventDefault();
            handleSubmit();
          }}
        >
          <Field label="ФИО" required error={error ?? undefined}>
            {(id, describedBy) => (
              <TextInput
                id={id}
                aria-describedby={describedBy}
                value={fullName}
                invalid={Boolean(error)}
                onChange={(event) => setFullName(event.target.value)}
              />
            )}
          </Field>
          <Field label="Телефон">
            {(id) => (
              <TextInput
                id={id}
                value={phone}
                onChange={(event) => setPhone(event.target.value)}
              />
            )}
          </Field>
          <Field label="Email">
            {(id) => (
              <TextInput
                id={id}
                type="email"
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            )}
          </Field>
          <Field label="Источник">
            {(id) => (
              <SelectInput
                id={id}
                value={source}
                onChange={(event) => setSource(event.target.value as CandidateSource)}
              >
                {(Object.keys(SOURCE_LABELS) as CandidateSource[]).map((item) => (
                  <option key={item} value={item}>
                    {SOURCE_LABELS[item]}
                  </option>
                ))}
              </SelectInput>
            )}
          </Field>
          <Field label="Должность">
            {(id) => (
              <TextInput
                id={id}
                value={position}
                onChange={(event) => setPosition(event.target.value)}
              />
            )}
          </Field>
          {canPickOwner && (
            <Field label="Ответственный HR">
              {(id) => (
                <SelectInput
                  id={id}
                  value={ownerId}
                  onChange={(event) => setOwnerId(event.target.value)}
                >
                  <option value="">Я ({user.username})</option>
                  {directory.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.full_name || item.username}
                    </option>
                  ))}
                </SelectInput>
              )}
            </Field>
          )}
        </form>
      </Modal>

      {duplicate && (
        <DuplicateResolveDialog
          duplicates={duplicate.error.duplicates}
          busy={saving}
          onCancel={() => setDuplicate(null)}
          onConfirm={() => void submit(duplicate.payload, true)}
          onOpenMatch={(id) => {
            setDuplicate(null);
            onClose();
            onOpenCandidate(id);
          }}
        />
      )}
    </>
  );
}
