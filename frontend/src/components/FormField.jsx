export function FormField({ label, htmlFor, hint, hintAfter = false, children, className = "" }) {
  const classes = ["form-field", className].filter(Boolean).join(" ");
  const hintEl = hint ? <p className="form-hint">{hint}</p> : null;
  return (
    <div className={classes}>
      {label ? (
        <label className="form-label" htmlFor={htmlFor}>
          {label}
        </label>
      ) : null}
      {!hintAfter ? hintEl : null}
      {children}
      {hintAfter ? hintEl : null}
    </div>
  );
}

let checkboxId = 0;

export function CheckboxField({
  id,
  label,
  hint,
  checked,
  onChange,
  disabled = false,
  className = ""
}) {
  const inputId = id || `cb-${++checkboxId}`;
  const classes = ["checkbox-field", disabled ? "is-disabled" : "", className]
    .filter(Boolean)
    .join(" ");

  return (
    <label className={classes} htmlFor={inputId}>
      <input
        id={inputId}
        type="checkbox"
        className="checkbox-field__input"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        disabled={disabled}
      />
      <span className="checkbox-field__text">
        <span className="checkbox-field__label">{label}</span>
        {hint ? <span className="checkbox-field__hint">{hint}</span> : null}
      </span>
    </label>
  );
}
