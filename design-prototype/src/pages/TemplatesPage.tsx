import { PageHeader } from "../components/ui/PageHeader";
import { Badge } from "../components/ui/StatusChip";
import { Icon } from "../icons/Icon";
import { Button } from "../components/ui/Button";
import { useAppState } from "../state/AppState";
import "./templatesPage.css";

interface TemplateItem {
  id: string;
  title: string;
  category: string;
  status: "published" | "draft" | "archived";
  version: string;
  updatedBy: string;
}

const TEMPLATES: TemplateItem[] = [
  { id: "t1", title: "Скрипт первичного звонка", category: "Скрипты", status: "published", version: "v3.1", updatedBy: "Елена Гурьева" },
  { id: "t2", title: "Анкета кандидата (общая)", category: "Анкеты", status: "published", version: "v2.0", updatedBy: "Игорь Белов" },
  { id: "t3", title: "Оффер-письмо (шаблон)", category: "Документы", status: "draft", version: "v1.4", updatedBy: "Елена Гурьева" },
  { id: "t4", title: "Анкета для стажёров", category: "Анкеты", status: "draft", version: "v0.3", updatedBy: "Марина Ковалёва" },
  { id: "t5", title: "Скрипт отказа кандидату", category: "Скрипты", status: "archived", version: "v1.0", updatedBy: "Игорь Белов" },
  { id: "t6", title: "Форма обратной связи после интервью", category: "Формы", status: "published", version: "v1.2", updatedBy: "Анна Смирнова" },
];

const STATUS_LABEL: Record<TemplateItem["status"], string> = {
  published: "Опубликован",
  draft: "Черновик",
  archived: "В архиве",
};

const STATUS_TONE: Record<TemplateItem["status"], "success" | "amber" | "neutral"> = {
  published: "success",
  draft: "amber",
  archived: "neutral",
};

export function TemplatesPage() {
  const { pushToast } = useAppState();
  return (
    <div>
      <PageHeader
        title="Шаблоны"
        description="Формы, анкеты, скрипты и документы — версионируемые сущности с draft/published/archived."
        actions={<Button variant="primary" icon="plus" onClick={() => pushToast("info", "Загрузка новой версии — предмет этапа 6 роадмапа (мок).")}>Новая версия</Button>}
      />

      <div className="template-list">
        {TEMPLATES.map((t) => (
          <div className="template-row" key={t.id}>
            <span className="template-icon"><Icon name="file-text" size={16} /></span>
            <div className="template-info">
              <span className="template-title">{t.title}</span>
              <span className="template-meta">{t.category} · {t.version} · обновил {t.updatedBy}</span>
            </div>
            <Badge tone={STATUS_TONE[t.status]}>{STATUS_LABEL[t.status]}</Badge>
          </div>
        ))}
      </div>
    </div>
  );
}
