"""
Forms used by the salesman field app (Phase 2).

The salesman flow has two ledger entry forms (`SaleForm` for Udhar,
`PaymentForm` for Jama) and a delete-confirm form that requires a
human-typed reason. All three are model-aware so the model layer's
validators (positive amount, deleted_reason rule) are honored.
"""

from django import forms

from .models import Payment, Sale


_AMOUNT_INPUT_CLS = (
    "w-full px-4 py-4 text-3xl font-bold border border-slate-300 rounded-lg "
    "focus:outline-none focus:ring-2 focus:border-transparent"
)
_TEXT_INPUT_CLS = (
    "w-full px-3 py-3 text-base border border-slate-300 rounded-lg "
    "focus:outline-none focus:ring-2 focus:border-transparent focus:ring-red-500"
)


class SaleForm(forms.ModelForm):
    """Udhar entry."""

    class Meta:
        model = Sale
        fields = ("amount", "notes")
        widgets = {
            "amount": forms.NumberInput(attrs={
                "inputmode": "decimal",
                "step": "0.01",
                "min": "0.01",
                "class": _AMOUNT_INPUT_CLS + " focus:ring-red-500",
                "placeholder": "0",
                "autofocus": "autofocus",
            }),
            "notes": forms.TextInput(attrs={
                "class": _TEXT_INPUT_CLS,
                "placeholder": "Notes (optional)",
                "maxlength": "500",
            }),
        }


class PaymentForm(forms.ModelForm):
    """Jama entry.

    Pass ``require_edit_reason=True`` to attach a non-model ``reason``
    field that must be filled in. The view reads ``form.cleaned_data["reason"]``
    and forwards it to :func:`core.audit.log_change`. This is how
    AuditLog.reason gets populated for salesman edits — the WHY of every
    edit is captured next to the WHAT.
    """

    reason = forms.CharField(
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={
            "rows": 2,
            "class": (
                "w-full px-3 py-3 text-base border border-slate-300 rounded-lg "
                "focus:outline-none focus:ring-2 focus:ring-green-500 focus:border-transparent"
            ),
            "placeholder": "Edit karne ka reason batayein…",
        }),
        error_messages={
            "required": "Edit ka reason zaroori hai.",
        },
    )

    class Meta:
        model = Payment
        fields = ("amount", "mode", "notes")
        widgets = {
            "amount": forms.NumberInput(attrs={
                "inputmode": "decimal",
                "step": "0.01",
                "min": "0.01",
                "class": _AMOUNT_INPUT_CLS + " focus:ring-green-500",
                "placeholder": "0",
                "autofocus": "autofocus",
            }),
            "mode": forms.RadioSelect(),
            "notes": forms.TextInput(attrs={
                "class": _TEXT_INPUT_CLS.replace("focus:ring-red-500", "focus:ring-green-500"),
                "placeholder": "Notes (optional)",
                "maxlength": "500",
            }),
        }

    def __init__(self, *args, require_edit_reason: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        # Django's ModelForm prepends a blank `('', '---------')` to a required
        # CharField+choices via `BlankChoiceIterator`, which renders as a third
        # radio button alongside Cash and UPI. Our template's cash-else
        # conditional then mislabels that empty radio as a phantom "UPI".
        # The field is required, so the validator already enforces a choice —
        # the UI doesn't need a "no choice yet" placeholder.
        self.fields["mode"].choices = Payment.Mode.choices

        # Promote the always-attached `reason` field to required when the
        # view tells us this is an edit. Keeps the create path frictionless
        # (no reason needed; the create itself is the WHY) while making
        # every edit explain itself in the audit log.
        self._require_edit_reason = require_edit_reason
        if not require_edit_reason:
            self.fields.pop("reason", None)

    def clean_reason(self):
        # Only called when the field is present (i.e. require_edit_reason).
        reason = (self.cleaned_data.get("reason") or "").strip()
        if not reason:
            raise forms.ValidationError("Edit ka reason zaroori hai.")
        return reason


class DeleteEntryForm(forms.Form):
    """Soft-delete confirm form — requires a typed reason."""

    reason = forms.CharField(
        max_length=500,
        widget=forms.Textarea(attrs={
            "rows": 3,
            "class": (
                "w-full px-3 py-3 text-base border border-slate-300 rounded-lg "
                "focus:outline-none focus:ring-2 focus:ring-red-500 focus:border-transparent"
            ),
            "placeholder": "Delete karne ka reason batayein…",
            "autofocus": "autofocus",
        }),
        error_messages={
            "required": "Reason zaroori hai.",
        },
    )

    def clean_reason(self):
        reason = (self.cleaned_data.get("reason") or "").strip()
        if not reason:
            raise forms.ValidationError("Reason zaroori hai.")
        return reason
