import { useEffect, useState } from "react";
import { api } from "../api";
import { CheckboxField, FormField } from "../components/FormField";

const TABS = [
  { id: "classification", label: "Classificazione" },
  { id: "addresses", label: "Indirizzi" },
  { id: "report", label: "Modifica messaggi" },
  { id: "scores", label: "Punteggi regole" },
  { id: "network", label: "Rete & Amavis" }
];

function linesToList(text) {
  return text
    .split("\n")
    .map((x) => x.trim())
    .filter(Boolean);
}

function listToLines(items) {
  return (items || []).join("\n");
}

function scoresToText(scores) {
  return Object.entries(scores || {})
    .map(([rule, value]) => `${rule}=${value}`)
    .join("\n");
}

function textToScores(text) {
  const scores = {};
  for (const row of linesToList(text)) {
    const eq = row.indexOf("=");
    if (eq <= 0) continue;
    const rule = row.slice(0, eq).trim();
    const value = Number(row.slice(eq + 1).trim());
    if (rule && !Number.isNaN(value)) scores[rule] = value;
  }
  return scores;
}

export default function SpamPage({ onError }) {
  const [tab, setTab] = useState("classification");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [settings, setSettings] = useState(null);
  const [whitelistText, setWhitelistText] = useState("");
  const [blacklistText, setBlacklistText] = useState("");
  const [whitelistToText, setWhitelistToText] = useState("");
  const [scoreText, setScoreText] = useState("");

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      try {
        const s = await api("/spamassassin");
        if (cancelled) return;
        setSettings(s);
        setWhitelistText(listToLines(s.whitelist_from));
        setBlacklistText(listToLines(s.blacklist_from));
        setWhitelistToText(listToLines(s.whitelist_to));
        setScoreText(scoresToText(s.scores));
        if (onError) onError("");
      } catch (err) {
        if (!cancelled && onError) onError(err.message || String(err));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, []);

  function patchClassification(patch) {
    setSettings((prev) => ({
      ...prev,
      classification: { ...prev.classification, ...patch }
    }));
  }

  function patchReport(patch) {
    setSettings((prev) => ({
      ...prev,
      report: { ...prev.report, ...patch }
    }));
  }

  function patchNetwork(patch) {
    setSettings((prev) => ({
      ...prev,
      network: { ...prev.network, ...patch }
    }));
  }

  function patchAmavis(patch) {
    setSettings((prev) => ({
      ...prev,
      amavis: { ...prev.amavis, ...patch }
    }));
  }

  async function save(event) {
    event.preventDefault();
    if (!settings) return;
    setSaving(true);
    setSaved(false);
    try {
      const payload = {
        ...settings,
        whitelist_from: linesToList(whitelistText),
        blacklist_from: linesToList(blacklistText),
        whitelist_to: linesToList(whitelistToText),
        scores: textToScores(scoreText)
      };
      await api("/spamassassin", {
        method: "PUT",
        body: JSON.stringify(payload)
      });
      setSaved(true);
      const refreshed = await api("/spamassassin");
      setSettings(refreshed);
      setWhitelistText(listToLines(refreshed.whitelist_from));
      setBlacklistText(listToLines(refreshed.blacklist_from));
      setWhitelistToText(listToLines(refreshed.whitelist_to));
      setScoreText(scoresToText(refreshed.scores));
      if (onError) onError("");
    } catch (err) {
      if (onError) onError(err.message || String(err));
    } finally {
      setSaving(false);
    }
  }

  if (loading || !settings) {
    return (
      <>
        <header className="page-header">
          <h2>SpamAssassin</h2>
          <p>Configurazione antispam globale (stile Webmin).</p>
        </header>
        <p className="empty-state">Caricamento configurazione...</p>
      </>
    );
  }

  const clf = settings.classification;
  const rep = settings.report;
  const net = settings.network;
  const av = settings.amavis;

  return (
    <>
      <header className="page-header">
        <h2>SpamAssassin</h2>
        <p>
          Policy antispam globali per tutto lo stack. Le sezioni seguono l&apos;organizzazione del
          modulo Webmin: classificazione, indirizzi, report, punteggi e rete.
        </p>
      </header>

      <nav className="spam-tabs" aria-label="Sezioni SpamAssassin">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            className={`spam-tab${tab === t.id ? " active" : ""}`}
            onClick={() => setTab(t.id)}
            aria-current={tab === t.id ? "true" : undefined}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <form onSubmit={save}>
        {tab === "classification" && (
          <div className="panel spam-panel">
            <h3>Classificazione spam</h3>
            <p className="panel-hint">
              SpamAssassin assegna un punteggio a ogni messaggio. Sopra la soglia il messaggio è
              considerato spam (integrato con Amavis).
            </p>
            <div className="form-grid form-grid-2">
              <FormField
                label="Soglia spam (required_score)"
                hint="Punteggio minimo per classificare come spam. Valori più alti = meno sensibile (es. 5 predefinito, 8.5 meno aggressivo)."
              >
                <input
                  type="number"
                  step="0.1"
                  min="0.1"
                  max="50"
                  value={clf.required_score}
                  onChange={(e) => patchClassification({ required_score: Number(e.target.value) })}
                />
              </FormField>
              <FormField label="Timeout query RBL (secondi)">
                <input
                  type="number"
                  min="1"
                  max="120"
                  value={clf.rbl_timeout}
                  onChange={(e) => patchClassification({ rbl_timeout: Number(e.target.value) })}
                />
              </FormField>
              <FormField label="Controlli MX sul mittente (0 = disattivato)">
                <input
                  type="number"
                  min="0"
                  max="10"
                  value={clf.mx_check_count}
                  onChange={(e) => patchClassification({ mx_check_count: Number(e.target.value) })}
                />
              </FormField>
              <FormField label="Pausa tra controlli MX (secondi)">
                <input
                  type="number"
                  min="0"
                  max="60"
                  value={clf.mx_check_delay}
                  onChange={(e) => patchClassification({ mx_check_delay: Number(e.target.value) })}
                />
              </FormField>
            </div>
            <div className="checkbox-group">
              <CheckboxField
                label="Usa classificatore Bayesiano"
                hint="Apprendimento automatico da messaggi spam/ham."
                checked={clf.use_bayes}
                onChange={(v) => patchClassification({ use_bayes: v })}
              />
              <CheckboxField
                label="Auto-apprendimento Bayes"
                checked={clf.bayes_auto_learn}
                onChange={(v) => patchClassification({ bayes_auto_learn: v })}
              />
              <CheckboxField
                label="Usa test di rete (DNSBL/RBL)"
                checked={clf.use_network_tests}
                onChange={(v) => patchClassification({ use_network_tests: v })}
              />
              <CheckboxField
                label="Salta controllo RBL open-relay"
                checked={clf.skip_rbl}
                onChange={(v) => patchClassification({ skip_rbl: v })}
              />
            </div>
            <FormField
              label="Reti e host attendibili (trusted_networks)"
              hint="Spazi o virgole tra le reti, es. 127.0.0.0/8 192.168.0.0/16"
            >
              <input
                type="text"
                value={clf.trusted_networks}
                onChange={(e) => patchClassification({ trusted_networks: e.target.value })}
              />
            </FormField>
            <FormField
              label="Lingue non considerate spam"
              hint='Valore "all" oppure codici ISO separati da spazio/virgola (es. it en).'
            >
              <input
                type="text"
                value={clf.ok_languages}
                onChange={(e) => patchClassification({ ok_languages: e.target.value })}
              />
            </FormField>
            <FormField label="Charset non considerati spam" hint='Valore "all" oppure elenco charset.'>
              <input
                type="text"
                value={clf.ok_locales}
                onChange={(e) => patchClassification({ ok_locales: e.target.value })}
              />
            </FormField>
          </div>
        )}

        {tab === "addresses" && (
          <>
            <div className="panel spam-panel">
              <h3>Mittenti consentiti e negati</h3>
              <p className="panel-hint">
                Queste regole valgono per il <strong>mittente</strong> del messaggio (
                <code>MAIL FROM</code> / intestazione <code>From:</code>), <em>non</em> per il
                dominio del destinatario. Per accettare tutta la posta verso un dominio locale
                (catch-all) usa <strong>Relay tutta la posta in ingresso</strong> nella pagina
                Domini.
              </p>
              <p className="panel-hint">
                Supporta wildcard in stile SpamAssassin: <code>*@dominio.it</code> (solo quel
                dominio), <code>*.dominio.it</code> o <code>*dominio.it</code> (dominio e
                sottodomini), <code>utente@dominio.it</code> (indirizzo esatto). La whitelist
                viene applicata in SpamAssassin e in Amavis (<code>@score_sender_maps</code>,
                punteggio -100).
              </p>
              <FormField label="Mittenti da non classificare mai come spam (whitelist_from)">
                <textarea
                  rows={6}
                  value={whitelistText}
                  onChange={(e) => setWhitelistText(e.target.value)}
                  placeholder="noreply@dominio.it&#10;*@partner.example&#10;*.amazon.com"
                />
              </FormField>
              <FormField label="Mittenti da classificare sempre come spam (blacklist_from)">
                <textarea
                  rows={6}
                  value={blacklistText}
                  onChange={(e) => setBlacklistText(e.target.value)}
                />
              </FormField>
            </div>
            <div className="panel spam-panel">
              <h3>Destinatari che accettano spam</h3>
              <p className="panel-hint">
                Indirizzi <code>To:</code> / <code>Cc:</code> per cui è consentito ricevere messaggi
                già classificati come spam (whitelist_to).
              </p>
              <FormField label="Destinatari (uno per riga)">
                <textarea
                  rows={4}
                  value={whitelistToText}
                  onChange={(e) => setWhitelistToText(e.target.value)}
                />
              </FormField>
            </div>
          </>
        )}

        {tab === "report" && (
          <div className="panel spam-panel">
            <h3>Modifica messaggi</h3>
            <p className="panel-hint">
              Opzioni su intestazioni e corpo dei messaggi analizzati e classificati come spam.
            </p>
            <div className="checkbox-group">
              <CheckboxField
                label="Modifica intestazione Subject dei messaggi spam"
                checked={rep.rewrite_subject}
                onChange={(v) => patchReport({ rewrite_subject: v })}
              />
              <CheckboxField
                label="Metti il messaggio originale in allegato (report_safe)"
                checked={rep.report_safe}
                onChange={(v) => patchReport({ report_safe: v })}
              />
              <CheckboxField
                label="Report solo in intestazione X-Spam-Status (non nel corpo)"
                checked={rep.report_header_only}
                onChange={(v) => patchReport({ report_header_only: v })}
              />
              <CheckboxField
                label="Aggiungi intestazione X-Spam-Level"
                checked={rep.add_spam_level_header}
                onChange={(v) => patchReport({ add_spam_level_header: v })}
              />
              <CheckboxField
                label="Report in modalità concisa (report_terse)"
                checked={rep.report_terse}
                onChange={(v) => patchReport({ report_terse: v })}
              />
              <CheckboxField
                label="Sostituisci template report predefinito"
                checked={rep.clear_report_template}
                onChange={(v) => patchReport({ clear_report_template: v })}
              />
            </div>
            <FormField label="Testo da anteporre al Subject">
              <input
                type="text"
                value={rep.subject_tag}
                onChange={(e) => patchReport({ subject_tag: e.target.value })}
              />
            </FormField>
            <FormField label="Carattere X-Spam-Level (un solo carattere)">
              <input
                type="text"
                maxLength={1}
                value={rep.spam_level_char}
                onChange={(e) => patchReport({ spam_level_char: e.target.value || "*" })}
              />
            </FormField>
            {rep.clear_report_template && (
              <FormField label="Testo report personalizzato">
                <textarea
                  rows={4}
                  value={rep.report_body}
                  onChange={(e) => patchReport({ report_body: e.target.value })}
                />
              </FormField>
            )}
          </div>
        )}

        {tab === "scores" && (
          <div className="panel spam-panel">
            <h3>Punteggi regole personalizzate</h3>
            <p className="panel-hint">
              Modifica il punteggio di test SpamAssassin esistenti o personalizzati. Una regola per
              riga, formato <code>NOME_REGOLA=valore</code> (es. <code>RCVD_IN_DNSWL_NONE=-0.5</code>
              ).
            </p>
            <textarea rows={14} value={scoreText} onChange={(e) => setScoreText(e.target.value)} />
          </div>
        )}

        {tab === "network" && (
          <div className="panel spam-panel">
            <h3>Rete e integrazione Amavis</h3>
            <p className="panel-hint">
              Impostazioni di rete per SpamAssassin e soglie operative di Amavis (tag / kill / DSN).
            </p>
            <div className="checkbox-group">
              <CheckboxField
                label="SpamAssassin può eseguire lookup DNS"
                checked={net.dns_available}
                onChange={(v) => patchNetwork({ dns_available: v })}
              />
            </div>
            <hr className="spam-divider" />
            <h4 className="spam-subheading">Soglie Amavis</h4>
            <p className="panel-hint">
              <code>tag2</code> di solito coincide con la soglia spam; <code>kill</code> è il livello
              di rifiuto/quarantena.
            </p>
            <div className="form-grid form-grid-2">
              <FormField label="Livello tag informativo (tag_level)">
                <input
                  type="number"
                  step="0.1"
                  value={av.tag_level}
                  onChange={(e) => patchAmavis({ tag_level: Number(e.target.value) })}
                />
              </FormField>
              <FormField label="Livello kill (rifiuto)">
                <input
                  type="number"
                  step="0.1"
                  min="1"
                  value={av.kill_level}
                  onChange={(e) => patchAmavis({ kill_level: Number(e.target.value) })}
                  disabled={av.sync_kill_with_score}
                />
              </FormField>
              <FormField label="Cutoff DSN">
                <input
                  type="number"
                  step="0.1"
                  value={av.dsn_cutoff_level}
                  onChange={(e) => patchAmavis({ dsn_cutoff_level: Number(e.target.value) })}
                />
              </FormField>
            </div>
            <div className="checkbox-group">
              <CheckboxField
                label="Calcola kill_level automaticamente (soglia spam + 2)"
                checked={av.sync_kill_with_score}
                onChange={(v) => patchAmavis({ sync_kill_with_score: v })}
              />
            </div>
          </div>
        )}

        <div className="panel spam-actions">
          <button type="submit" className="btn-primary" disabled={saving}>
            {saving ? "Salvataggio..." : "Applica modifiche"}
          </button>
          {saved && <span className="spam-saved">Configurazione salvata e file rigenerati.</span>}
          <p className="panel-hint" style={{ marginTop: "0.75rem", marginBottom: 0 }}>
            Dopo il salvataggio, Postfix/Amavis ricaricano i file generati in{" "}
            <code>config/spamassassin/</code> e <code>config/amavis/</code> sul host. Amavis esegue{" "}
            <code>reload</code> automaticamente entro
            ~15 secondi quando cambiano le regole spam.
          </p>
        </div>
      </form>
    </>
  );
}
