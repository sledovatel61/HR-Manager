import { useEffect, useState } from "react";
import { apiFetch } from "../../api";

export function DocumentRulesPage() {
  const [lists, setLists] = useState<Array<{id:string;name:string;position?:string;stage?:string}>>([]);
  const [error,setError]=useState(""); const [loading,setLoading]=useState(true);
  const load=()=>{setLoading(true);apiFetch("/document-lists").then(r=>r.json()).then(setLists).catch(()=>setError("Не удалось загрузить списки. Повторить")).finally(()=>setLoading(false));};
  useEffect(load,[]);
  return <section aria-labelledby="documents-title"><h1 id="documents-title">Документы и мои правила</h1><p>Версии списков неизменяемы после публикации. Файлы документов не загружаются.</p>{loading&&<p role="status">Загрузка…</p>}{error&&<button onClick={()=>{setError("");load()}}>{error}</button>}{!loading&&!error&&!lists.length&&<p>Списков пока нет.</p>}{lists.map(l=><article key={l.id}><h2>{l.name}</h2><p>{l.position||"Общий список"}{l.stage&&` · этап: ${l.stage}`}</p></article>)}</section>;
}
export default DocumentRulesPage;
