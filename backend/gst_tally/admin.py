from django.contrib import admin
from django import forms
from django.contrib import messages

from .auth_service import secret_hash
from .models import DeviceActivation, License, UserProfile


class LicenseAdminForm(forms.ModelForm):
    activation_key = forms.CharField(
        label="Activation Key", required=False, widget=forms.PasswordInput(render_value=False),
        help_text="Required for a new license. Leave blank while editing to keep the existing key.",
    )

    class Meta:
        model = License
        fields = ("serial_number", "registered_email", "activation_key", "status", "expiry_date", "max_devices")

    def clean_activation_key(self):
        raw_key = self.cleaned_data.get("activation_key", "").strip()
        if not self.instance.pk and not raw_key:
            raise forms.ValidationError("Activation key is required.")
        if raw_key and License.objects.filter(activation_key_hash=secret_hash(raw_key)).exclude(pk=self.instance.pk).exists():
            raise forms.ValidationError("This activation key is already in use.")
        return raw_key

    def save(self, commit=True):
        license_obj = super().save(commit=False)
        raw_key = self.cleaned_data.get("activation_key")
        if raw_key:
            license_obj.activation_key_hash = secret_hash(raw_key)
        license_obj.registered_email = license_obj.registered_email.strip().lower()
        if commit:
            license_obj.save()
            self.save_m2m()
        return license_obj


@admin.register(License)
class LicenseAdmin(admin.ModelAdmin):
    form = LicenseAdminForm
    list_display = ("serial_number", "registered_email", "status", "is_activated", "activated_user", "max_devices", "expiry_date")
    list_filter = ("status", "is_activated")
    search_fields = ("serial_number", "registered_email", "activated_user__email")
    readonly_fields = ("is_activated", "activated_user", "activated_at", "created_at", "updated_at")
    fields = ("serial_number", "registered_email", "activation_key", "status", "expiry_date", "max_devices", "is_activated", "activated_user", "activated_at", "created_at", "updated_at")

    def response_add(self, request, obj, post_url_continue=None):
        messages.success(request, "License Created Successfully")
        return super().response_add(request, obj, post_url_continue)


@admin.register(DeviceActivation)
class DeviceActivationAdmin(admin.ModelAdmin):
    list_display = ("user", "device_name", "device_id", "license", "is_active", "activated_at", "last_used_at")
    list_filter = ("is_active",)
    search_fields = ("user__username", "user__email", "device_id")
    readonly_fields = ("user", "license", "device_id", "device_name", "activated_at", "last_used_at")
    fields = ("user", "license", "device_id", "device_name", "is_active", "activated_at", "last_used_at")


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone_number", "activated_at")
    search_fields = ("user__username", "user__email", "phone_number")
    readonly_fields = ("activated_at",)
