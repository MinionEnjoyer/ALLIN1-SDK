// EnhancedSmokeController.cs -- custom smoke and selectable colour signals.

using System;
using System.Collections.Generic;
using System.IO;
using GTA;
using GTA.Math;
using GTA.Native;

namespace ALLIN1
{
    internal static class EnhancedSmokePolicy
    {
        internal static bool IsCustomSmokeProjectile(
            int projectileWeaponHash, int customSmokeWeaponHash)
        {
            return projectileWeaponHash == customSmokeWeaponHash;
        }

        internal static bool ShouldEmitPulse(
            int now, int activateAt, int nextPulseAt, int expiresAt)
        {
            return unchecked(now - activateAt) >= 0 &&
                unchecked(now - nextPulseAt) >= 0 &&
                unchecked(now - expiresAt) < 0;
        }

        internal static bool IsExpired(int now, int expiresAt)
        {
            return unchecked(now - expiresAt) >= 0;
        }

        internal static bool ShouldConsumeCustomProjectile(
            int elapsedMs, int stationaryElapsedMs, float speed,
            float verticalSpeed, float displacement,
            bool isInAir, bool hasCollided)
        {
            return elapsedMs >= 500 &&
                stationaryElapsedMs >= 300 &&
                speed <= 0.35f &&
                Math.Abs(verticalSpeed) <= 0.18f &&
                displacement <= 0.08f &&
                !isInAir && hasCollided;
        }

        internal static bool IsDuplicateField(
            float distance, int now, int existingExpiresAt)
        {
            return distance <= 8f &&
                unchecked(existingExpiresAt - now) > 0;
        }

        internal static string NormalizeColorName(string requested)
        {
            string color = (requested ?? "").Trim().ToLowerInvariant();
            switch (color)
            {
                case "red":
                case "orange":
                case "yellow":
                case "green":
                case "blue":
                case "purple":
                case "white":
                    return color;
                default:
                    return "white";
            }
        }

        internal static string ColorForReason(
            string reason, string playerColor, bool playerOwned)
        {
            if (string.Equals(reason, "casualty_extraction",
                    StringComparison.OrdinalIgnoreCase) ||
                (reason ?? "").IndexOf("casevac",
                    StringComparison.OrdinalIgnoreCase) >= 0)
                return "orange";
            return playerOwned ? NormalizeColorName(playerColor) : "white";
        }

        internal static int SupplementalPulseIntervalMs(int elapsedMs)
        {
            return elapsedMs < 4500 ? 140 : 360;
        }

        internal static int SupplementalEmitterCount(int elapsedMs)
        {
            return elapsedMs < 4500 ? 4 : 2;
        }

        internal static float SupplementalScale(
            int elapsedMs, string colorName)
        {
            float nativeScale = elapsedMs < 4500 ? 1.45f : 1.10f;
            return NormalizeColorName(colorName) == "white"
                ? nativeScale : nativeScale * 2f;
        }

        internal static float SupplementalRadius(int variation)
        {
            return 2.15f + 0.45f * Math.Max(0, variation % 3);
        }

        internal static bool ShouldLogSupplementalPulse(int pulseIndex)
        {
            return pulseIndex > 0 &&
                (pulseIndex == 1 || pulseIndex % 20 == 0);
        }

        internal static bool ShouldUsePrimaryLoop(string colorName)
        {
            return NormalizeColorName(colorName) == "white";
        }

        internal static bool CanUseNativeFallback(string colorName)
        {
            return NormalizeColorName(colorName) == "white";
        }
    }

    public sealed class EnhancedSmokeController : Script
    {
        private const float ProjectileScanRadius = 180f;
        private const int ProjectileForgetMs = 30000;
        private const int GenericActivationDelayMs = 0;
        private const int ScriptedActivationDelayMs = 1100;
        private const int PulseIntervalMs = 5000;
        private const int FieldDurationMs = 40000;
        private const int HeartbeatIntervalMs = 5000;
        private const int ProjectileScanIntervalMs = 100;
        private const int InventorySyncIntervalMs = 500;
        private const int MaximumActiveFields = 8;
        private const int SmokeGrenadeExplosionType = 20;
        private const int MissingProjectileGraceMs = 400;
        private const int ProjectileMotionLogIntervalMs = 500;
        private const string SmokeParticleAsset = "core";
        private const string SmokeParticleEffect = "exp_grd_bzgas_smoke";
        private const string SupplementalParticleAsset = "scr_carsteal4";
        private const string SupplementalParticleEffect =
            "scr_carsteal4_wheel_burnout";

        // GTA ships a dedicated smoke-grenade carrier separate from BZ gas.
        // Keeping these hashes separate preserves native Tear Gas unchanged.
        private static EnhancedSmokeController _current;

        private readonly Dictionary<int, TrackedProjectile> _projectiles =
            new Dictionary<int, TrackedProjectile>();
        private readonly Dictionary<int, int> _consumedProjectiles =
            new Dictionary<int, int>();
        private readonly List<SmokeField> _fields =
            new List<SmokeField>();
        private readonly Dictionary<int, ExpectedSmoke> _expectedSmokes =
            new Dictionary<int, ExpectedSmoke>();
        private readonly bool _enabled;
        private readonly bool _rpfTuningInstalled;
        private readonly bool _weaponDlcMarkerPresent;
        private readonly bool _mergedWeaponMarkerPresent;
        private readonly bool _weaponDlcInstalled;
        private readonly int _availableCustomWeaponTypes;
        private bool _particleAssetReady;
        private bool _supplementalParticleAssetReady;
        private int _lastHeartbeatAt;
        private int _nextProjectileScanAt;
        private int _nextInventorySyncAt;
        private long _projectilesTracked;
        private long _projectilesSettled;
        private long _projectilesExpiredUnsettled;
        private long _fieldsStarted;
        private long _fieldsDeduplicated;
        private long _colorOverlaps;
        private long _fieldsRejected;
        private long _pulses;
        private long _supplementalPulses;
        private long _supplementalEffects;
        private long _fieldsCompleted;
        private long _exceptions;

        private sealed class TrackedProjectile
        {
            internal Projectile Projectile;
            internal Vector3 LastPosition;
            internal int OwnerHandle;
            internal int StartedAt;
            internal int LastSeenAt;
            internal int StationarySinceAt;
            internal int LastMotionLogAt;
            internal string ColorName;
            internal bool FieldRegistered;
            internal float LastSpeed;
            internal float LastVerticalSpeed;
            internal float LastDisplacement;
            internal int LastStationaryElapsedMs;
            internal bool LastIsInAir;
            internal bool LastHasCollided;
            internal int WeaponHash;
            internal string WeaponName;
            internal string Reason;
        }

        private sealed class SmokeField
        {
            internal Vector3 Center;
            internal int ActivateAt;
            internal int NextFallbackAt;
            internal int NextSupplementalAt;
            internal int ExpiresAt;
            internal int PulseIndex;
            internal int SupplementalPulseIndex;
            internal int SupplementalEffectsEmitted;
            internal string Reason;
            internal int ThrowerHandle;
            internal SmokeColor Color;
            internal bool ParticleStartAttempted;
            internal bool BackendUnavailableReported;
            internal readonly List<int> ParticleHandles = new List<int>();
        }

        private sealed class ExpectedSmoke
        {
            internal string Reason;
            internal int ExpiresAt;
        }

        private sealed class SmokeColor
        {
            internal string Name;
            internal float Red;
            internal float Green;
            internal float Blue;
        }

        public EnhancedSmokeController()
        {
            _current = this;
            _enabled = NpcPhysicsExperiment.ReadBooleanSetting(
                "enhanced_smoke_effects", true);
            string marker = Path.Combine(
                AppDomain.CurrentDomain.BaseDirectory,
                "ALLIN1_smoke_tuning.json");
            _rpfTuningInstalled = File.Exists(marker);
            bool legacyWeaponDlcMarkerPresent = File.Exists(Path.Combine(
                AppDomain.CurrentDomain.BaseDirectory,
                "ALLIN1_colored_smoke_weapons.json"));
            _mergedWeaponMarkerPresent = File.Exists(Path.Combine(
                AppDomain.CurrentDomain.BaseDirectory,
                "ALLIN1_colored_smoke_merged_canary.json"));
            _weaponDlcMarkerPresent = legacyWeaponDlcMarkerPresent ||
                _mergedWeaponMarkerPresent;
            _availableCustomWeaponTypes =
                SmokeGrenadeCatalog.AvailableCustomWeaponCount();
            int registeredCustomWeaponTypes =
                SmokeGrenadeCatalog.RegisteredCustomWeaponCount(
                    out int totalDlcWeaponTypes,
                    out string dlcWeaponCatalogFailure);
            _weaponDlcInstalled = _weaponDlcMarkerPresent &&
                _availableCustomWeaponTypes ==
                    SmokeGrenadeCatalog.Products.Length;
            // Field animation stays frame-accurate while projectile scans and
            // inventory reconciliation are independently rate-limited below.
            Interval = _enabled ? 0 : 1000;
            Tick += OnTick;
            Aborted += OnAborted;
            PhysicsExperimentLog.Info(
                "enhanced_smoke_configuration_loaded",
                new Dictionary<string, object>
                {
                    { "enabled", _enabled },
                    { "projectile_scan_radius", ProjectileScanRadius },
                    { "field_duration_ms", FieldDurationMs },
                    { "pulse_interval_ms", PulseIntervalMs },
                    { "primary_particle_asset", SmokeParticleAsset },
                    { "primary_particle_effect", SmokeParticleEffect },
                    { "supplemental_particle_asset",
                        SupplementalParticleAsset },
                    { "supplemental_particle_effect",
                        SupplementalParticleEffect },
                    { "maximum_active_fields", MaximumActiveFields },
                    { "rpf_tuning_installed", _rpfTuningInstalled },
                    { "weapon_dlc_marker_present",
                        _weaponDlcMarkerPresent },
                    { "merged_weapon_marker_present",
                        _mergedWeaponMarkerPresent },
                    { "weapon_dlc_installed", _weaponDlcInstalled },
                    { "valid_custom_weapon_types",
                        _availableCustomWeaponTypes },
                    { "registered_custom_weapon_types",
                        registeredCustomWeaponTypes },
                    { "weapon_registration_mode",
                        registeredCustomWeaponTypes ==
                            SmokeGrenadeCatalog.Products.Length
                            ? "dlc_catalog" : "base_weapon_info" },
                    { "dlc_catalog_registration_required", false },
                    { "total_registered_dlc_weapon_types",
                        totalDlcWeaponTypes },
                    { "dlc_weapon_catalog_failure",
                        dlcWeaponCatalogFailure },
                    { "custom_weapon_types",
                        SmokeGrenadeCatalog.Products.Length },
                    { "casevac_smoke_color", "orange" },
                    { "rpf_edits_required", true },
                });
        }

        internal static bool RegisterScriptedSmoke(
            Vector3 center, int throwerHandle, string reason, int now)
        {
            EnhancedSmokeController current = _current;
            return current != null && current._enabled &&
                current.RegisterField(center, throwerHandle,
                    reason ?? "scripted", now,
                ScriptedActivationDelayMs,
                    current.ResolveSmokeColor(reason, throwerHandle));
        }

        internal static bool ExpectThrownSmoke(
            int throwerHandle, string reason, int now)
        {
            EnhancedSmokeController current = _current;
            if (current == null || !current._enabled || throwerHandle == 0)
                return false;
            current._expectedSmokes[throwerHandle] = new ExpectedSmoke
            {
                Reason = reason ?? "scripted_throw",
                ExpiresAt = unchecked(now + 10000),
            };
            PhysicsExperimentLog.Info("enhanced_smoke_throw_expected",
                new Dictionary<string, object>
                {
                    { "thrower", throwerHandle },
                    { "reason", reason ?? "scripted_throw" },
                    { "expires_in_ms", 10000 },
                });
            return true;
        }

        private void OnTick(object sender, EventArgs args)
        {
            if (!_enabled || Game.IsLoading) return;
            try
            {
                Ped player = Game.Player.Character;
                if (player == null || !player.Exists() || player.IsDead)
                    return;
                int now = Game.GameTime;
                EnsureParticleAssets();
                if (unchecked(now - _nextProjectileScanAt) >= 0)
                {
                    ScanSmokeProjectiles(player, now);
                    _nextProjectileScanAt = unchecked(
                        now + ProjectileScanIntervalMs);
                }
                if (_weaponDlcInstalled &&
                    unchecked(now - _nextInventorySyncAt) >= 0)
                {
                    CharacterInventory.SyncSmokeWeaponNow(player);
                    _nextInventorySyncAt = unchecked(
                        now + InventorySyncIntervalMs);
                }
                UpdateFields(now);
                PruneConsumedProjectiles(now);
                if (unchecked(now - _lastHeartbeatAt) >=
                    HeartbeatIntervalMs)
                {
                    WriteHeartbeat(now);
                    _lastHeartbeatAt = now;
                }
            }
            catch (Exception ex)
            {
                _exceptions++;
                PhysicsExperimentLog.Error(
                    "enhanced_smoke_tick_failed", ex);
                ClientLog.Error("ENHANCED-SMOKE", "OnTick", ex);
            }
        }

        private void OnAborted(object sender, EventArgs args)
        {
            _projectiles.Clear();
            _consumedProjectiles.Clear();
            _expectedSmokes.Clear();
            foreach (SmokeField field in _fields)
                StopFieldParticles(field);
            _fields.Clear();
            if (_particleAssetReady)
                Function.Call(Hash.REMOVE_NAMED_PTFX_ASSET,
                    SmokeParticleAsset);
            if (_supplementalParticleAssetReady)
                Function.Call(Hash.REMOVE_NAMED_PTFX_ASSET,
                    SupplementalParticleAsset);
            if (ReferenceEquals(_current, this)) _current = null;
        }

        private void ScanSmokeProjectiles(Ped player, int now)
        {
            Projectile[] nearby = World.GetNearbyProjectiles(
                player.Position, ProjectileScanRadius);
            var observed = new HashSet<int>();
            var settled = new List<int>();
            foreach (Projectile projectile in nearby)
            {
                int projectileWeaponHash = projectile == null
                    ? 0 : (int)projectile.WeaponHash;
                if (projectile == null || !projectile.Exists() ||
                    !SmokeGrenadeCatalog.TryGetByWeaponHash(
                        projectileWeaponHash,
                        out SmokeGrenadeProduct smokeProduct))
                    continue;
                int handle = projectile.Handle;
                observed.Add(handle);
                if (_consumedProjectiles.ContainsKey(handle)) continue;
                if (!_projectiles.TryGetValue(handle,
                        out TrackedProjectile tracked))
                {
                    Entity owner = projectile.OwnerEntity;
                    int ownerHandle = owner != null && owner.Exists()
                        ? owner.Handle : 0;
                    bool likelyUnresolvedPlayerThrow = ownerHandle == 0 &&
                        projectile.Position.DistanceTo(player.Position) <= 4f &&
                        Function.Call<int>(Hash.GET_SELECTED_PED_WEAPON,
                            player.Handle) == projectileWeaponHash;
                    bool playerOwned = ownerHandle == player.Handle ||
                        likelyUnresolvedPlayerThrow;
                    if (playerOwned && ownerHandle == 0)
                        ownerHandle = player.Handle;
                    string colorName = smokeProduct.ColorName;
                    int remainingStock =
                        CharacterInventory.GetSmokeQuantity(colorName);
                    bool stockConsumed = playerOwned &&
                        CharacterInventory.TryConsumeSmokeColor(
                            colorName, out remainingStock);
                    tracked = new TrackedProjectile
                    {
                        Projectile = projectile,
                        LastPosition = projectile.Position,
                        OwnerHandle = ownerHandle,
                        StartedAt = now,
                        LastSeenAt = now,
                        ColorName = colorName,
                        WeaponHash = projectileWeaponHash,
                        WeaponName = smokeProduct.WeaponName,
                        LastMotionLogAt = now,
                    };
                    if (_expectedSmokes.TryGetValue(ownerHandle,
                            out ExpectedSmoke expected) &&
                        unchecked(expected.ExpiresAt - now) > 0)
                    {
                        tracked.Reason = expected.Reason;
                        _expectedSmokes.Remove(ownerHandle);
                    }
                    _projectiles.Add(handle, tracked);
                    _projectilesTracked++;
                    PhysicsExperimentLog.Info(
                        "enhanced_smoke_projectile_tracked",
                        ProjectileFields(handle, tracked, now,
                            new Dictionary<string, object>
                            {
                                { "player_owned", playerOwned },
                                { "inventory_consumed", stockConsumed },
                                { "color", colorName ?? "white" },
                                { "remaining_stock", remainingStock },
                            }));
                }
                Vector3 currentPosition = projectile.Position;
                float displacement = currentPosition.DistanceTo(
                    tracked.LastPosition);
                Vector3 velocity = projectile.Velocity;
                float speed = velocity.Length();
                bool isInAir = Function.Call<bool>(
                    Hash.IS_ENTITY_IN_AIR, handle);
                bool hasCollided = Function.Call<bool>(
                    Hash.HAS_ENTITY_COLLIDED_WITH_ANYTHING, handle);
                tracked.Projectile = projectile;
                tracked.LastPosition = currentPosition;
                tracked.LastSeenAt = now;
                bool stableSample = speed <= 0.35f &&
                    Math.Abs(velocity.Z) <= 0.18f &&
                    displacement <= 0.08f &&
                    !isInAir && hasCollided;
                if (stableSample)
                {
                    if (tracked.StationarySinceAt == 0)
                        tracked.StationarySinceAt = now;
                }
                else
                {
                    tracked.StationarySinceAt = 0;
                }
                int stationaryElapsed = tracked.StationarySinceAt == 0
                    ? 0 : unchecked(now - tracked.StationarySinceAt);
                tracked.LastSpeed = speed;
                tracked.LastVerticalSpeed = velocity.Z;
                tracked.LastDisplacement = displacement;
                tracked.LastStationaryElapsedMs = stationaryElapsed;
                tracked.LastIsInAir = isInAir;
                tracked.LastHasCollided = hasCollided;
                if (unchecked(now - tracked.LastMotionLogAt) >=
                    ProjectileMotionLogIntervalMs)
                {
                    tracked.LastMotionLogAt = now;
                    PhysicsExperimentLog.Info(
                        "enhanced_smoke_projectile_motion",
                        ProjectileFields(handle, tracked, now,
                            MotionFields(tracked, null)));
                }
                if (!tracked.FieldRegistered &&
                    EnhancedSmokePolicy.ShouldConsumeCustomProjectile(
                        unchecked(now - tracked.StartedAt),
                        stationaryElapsed, speed, velocity.Z,
                        displacement, isInAir, hasCollided))
                    settled.Add(handle);
            }

            foreach (int handle in settled)
                ActivateTrackedProjectile(
                    handle, now, "smoke_grenade_settled");

            var disappeared = new List<int>();
            foreach (KeyValuePair<int, TrackedProjectile> pair in
                _projectiles)
            {
                if (observed.Contains(pair.Key)) continue;
                if (unchecked(now - pair.Value.LastSeenAt) >=
                    MissingProjectileGraceMs)
                    disappeared.Add(pair.Key);
            }
            foreach (int handle in disappeared)
                ConsumeProjectile(handle, now,
                    "smoke_grenade_detonated");
        }

        private void ConsumeProjectile(int handle, int now, string reason)
        {
            if (!_projectiles.TryGetValue(handle,
                    out TrackedProjectile tracked)) return;
            _projectiles.Remove(handle);
            _consumedProjectiles[handle] = unchecked(now +
                ProjectileForgetMs);
            bool fieldAlreadyRegistered = tracked.FieldRegistered;
            if (!fieldAlreadyRegistered)
            {
                _projectilesExpiredUnsettled++;
                PhysicsExperimentLog.Info(
                    "enhanced_smoke_projectile_expired_unsettled",
                    ProjectileFields(handle, tracked, now,
                        MotionFields(tracked,
                            new Dictionary<string, object>
                            {
                                { "reason", reason },
                                { "field_registered", false },
                            })));
            }
            PhysicsExperimentLog.Info("enhanced_smoke_projectile_consumed",
                ProjectileFields(handle, tracked, now,
                    new Dictionary<string, object>
                    {
                        { "reason", reason },
                        { "native_detonation_suppressed", false },
                        { "field_already_registered",
                            fieldAlreadyRegistered },
                        { "field_registered", fieldAlreadyRegistered },
                    }));
        }

        private void ActivateTrackedProjectile(
            int handle, int now, string reason)
        {
            if (!_projectiles.TryGetValue(handle,
                    out TrackedProjectile tracked) ||
                tracked.FieldRegistered) return;
            _projectilesSettled++;
            PhysicsExperimentLog.Info(
                "enhanced_smoke_projectile_settled",
                ProjectileFields(handle, tracked, now,
                    MotionFields(tracked,
                        new Dictionary<string, object>
                        {
                            { "settlement_required", true },
                            { "activation_position_locked", true },
                        })));
            RegisterTrackedField(tracked, reason, now);
            tracked.FieldRegistered = true;
            bool canisterRetired = RetireSettledCanister(tracked);
            _projectiles.Remove(handle);
            _consumedProjectiles[handle] = unchecked(now +
                ProjectileForgetMs);
            PhysicsExperimentLog.Info(
                "enhanced_smoke_projectile_field_activated",
                ProjectileFields(handle, tracked, now,
                    new Dictionary<string, object>
                    {
                        { "reason", reason },
                        { "canister_preserved", false },
                        { "canister_retired", canisterRetired },
                        { "rolling_audio_prevented", canisterRetired },
                        { "color", tracked.ColorName ?? "white" },
                    }));
        }

        private static bool RetireSettledCanister(
            TrackedProjectile tracked)
        {
            try
            {
                Projectile projectile = tracked?.Projectile;
                if (projectile == null || !projectile.Exists()) return true;
                // The stock throwable keeps a rolling-loop sound attached to
                // its physical entity. The custom field owns the visuals from
                // this point onward, so remove that entity as soon as it has
                // supplied its final settled position.
                projectile.Delete();
                return projectile == null || !projectile.Exists();
            }
            catch (Exception ex)
            {
                PhysicsExperimentLog.Error(
                    "enhanced_smoke_canister_retire_failed", ex);
                ClientLog.Error("ENHANCED-SMOKE",
                    "RetireSettledCanister", ex);
                return false;
            }
        }

        private void RegisterTrackedField(
            TrackedProjectile tracked, string reason, int now)
        {
            RegisterField(tracked.LastPosition, tracked.OwnerHandle,
                tracked.Reason ?? reason, now, GenericActivationDelayMs,
                GetSmokeColor(tracked.ColorName ??
                    ResolveSmokeColor(reason,
                        tracked.OwnerHandle).Name));
        }

        private bool RegisterField(
            Vector3 center, int throwerHandle, string reason,
            int now, int activationDelayMs, SmokeColor color)
        {
            foreach (SmokeField existing in _fields)
            {
                if (!EnhancedSmokePolicy.IsDuplicateField(
                        center.DistanceTo(existing.Center), now,
                        existing.ExpiresAt))
                    continue;
                bool upgradedToCasevac = color != null &&
                    color.Name == "orange" &&
                    existing.Color?.Name != "orange";
                bool sameColor = string.Equals(
                    color?.Name, existing.Color?.Name,
                    StringComparison.OrdinalIgnoreCase);
                if (!sameColor && !upgradedToCasevac)
                {
                    _colorOverlaps++;
                    PhysicsExperimentLog.Info(
                        "enhanced_smoke_color_overlap",
                        new Dictionary<string, object>
                        {
                            { "existing_color",
                                existing.Color?.Name ?? "white" },
                            { "new_color", color?.Name ?? "white" },
                            { "distance", center.DistanceTo(existing.Center) },
                            { "fields_kept_separate", true },
                        });
                    continue;
                }
                if (upgradedToCasevac)
                {
                    StopFieldParticles(existing);
                    existing.Color = color;
                    existing.Reason = reason ?? existing.Reason;
                    existing.ParticleStartAttempted = true;
                }
                _fieldsDeduplicated++;
                PhysicsExperimentLog.Info(
                    "enhanced_smoke_field_deduplicated",
                    new Dictionary<string, object>
                    {
                        { "reason", reason ?? "unspecified" },
                        { "distance", center.DistanceTo(existing.Center) },
                        { "existing_reason", existing.Reason },
                        { "upgraded_to_casevac", upgradedToCasevac },
                    });
                return false;
            }
            if (_fields.Count >= MaximumActiveFields)
            {
                _fieldsRejected++;
                PhysicsExperimentLog.Info(
                    "enhanced_smoke_field_rejected",
                    new Dictionary<string, object>
                    {
                        { "reason", "active_field_limit" },
                        { "active_fields", _fields.Count },
                    });
                return false;
            }
            int activateAt = unchecked(now + activationDelayMs);
            var field = new SmokeField
            {
                Center = center,
                ActivateAt = activateAt,
                NextFallbackAt = activateAt,
                NextSupplementalAt = activateAt,
                ExpiresAt = unchecked(activateAt + FieldDurationMs),
                Reason = reason ?? "unspecified",
                ThrowerHandle = throwerHandle,
                Color = color ?? GetSmokeColor("white"),
            };
            _fields.Add(field);
            _fieldsStarted++;
            PhysicsExperimentLog.Info("enhanced_smoke_field_started",
                SmokeFieldFields(field, now,
                    new Dictionary<string, object>
                    {
                        { "activation_delay_ms", activationDelayMs },
                        { "duration_ms", FieldDurationMs },
                        { "damage_scale", 0f },
                        { "color", field.Color.Name },
                        { "rpf_tuning_installed", _rpfTuningInstalled },
                        { "weapon_dlc_installed", _weaponDlcInstalled },
                        { "native_vfx_expected_suppressed",
                            _weaponDlcInstalled },
                        { "primary_backend",
                            SmokeParticleAsset + "/" +
                            SmokeParticleEffect },
                        { "supplemental_backend",
                            SupplementalParticleAsset + "/" +
                            SupplementalParticleEffect },
                        { "rpf_edits_required", true },
                    }));
            return true;
        }

        private void UpdateFields(int now)
        {
            for (int index = _fields.Count - 1; index >= 0; index--)
            {
                SmokeField field = _fields[index];
                if (EnhancedSmokePolicy.IsExpired(now, field.ExpiresAt))
                {
                    StopFieldParticles(field);
                    _fields.RemoveAt(index);
                    _fieldsCompleted++;
                    PhysicsExperimentLog.Info(
                        "enhanced_smoke_field_completed",
                        SmokeFieldFields(field, now, null));
                    continue;
                }
                if (unchecked(now - field.ActivateAt) < 0)
                    continue;
                bool usePrimaryLoop =
                    EnhancedSmokePolicy.ShouldUsePrimaryLoop(
                        field.Color?.Name);
                if (!field.ParticleStartAttempted &&
                    (!usePrimaryLoop || _particleAssetReady))
                {
                    field.ParticleStartAttempted = true;
                    int started = usePrimaryLoop
                        ? StartFieldParticles(field) : 0;
                    field.PulseIndex++;
                    _pulses++;
                    PhysicsExperimentLog.Info(
                        "enhanced_smoke_loop_started",
                        SmokeFieldFields(field, now,
                            new Dictionary<string, object>
                            {
                                { "requested_columns", 3 },
                                { "particle_handles", started },
                                { "particle_effect_started", started > 0 },
                                { "color", field.Color.Name },
                                { "primary_backend_enabled", usePrimaryLoop },
                                { "single_color_backend", !usePrimaryLoop },
                            }));
                }

                int elapsedMs = unchecked(now - field.ActivateAt);
                if (_supplementalParticleAssetReady &&
                    EnhancedSmokePolicy.ShouldEmitPulse(
                        now, field.ActivateAt,
                        field.NextSupplementalAt, field.ExpiresAt))
                {
                    EmitSupplementalPulse(field, now, elapsedMs);
                    field.NextSupplementalAt = unchecked(now +
                        EnhancedSmokePolicy.SupplementalPulseIntervalMs(
                            elapsedMs));
                }

                if (field.ParticleHandles.Count == 0 &&
                    field.SupplementalEffectsEmitted == 0 &&
                    EnhancedSmokePolicy.CanUseNativeFallback(
                        field.Color?.Name) &&
                    EnhancedSmokePolicy.ShouldEmitPulse(
                        now, field.ActivateAt, field.NextFallbackAt,
                        field.ExpiresAt))
                {
                    EmitFallbackPulse(field, now);
                    field.NextFallbackAt = unchecked(
                        now + PulseIntervalMs);
                }
                else if (field.ParticleHandles.Count == 0 &&
                    field.SupplementalEffectsEmitted == 0 &&
                    !EnhancedSmokePolicy.CanUseNativeFallback(
                        field.Color?.Name) &&
                    !field.BackendUnavailableReported &&
                    unchecked(now - field.ActivateAt) >= 1000)
                {
                    field.BackendUnavailableReported = true;
                    PhysicsExperimentLog.Info(
                        "enhanced_smoke_color_backend_unavailable",
                        SmokeFieldFields(field, now,
                            new Dictionary<string, object>
                            {
                                { "native_fallback_suppressed", true },
                                { "mixed_color_prevented", true },
                            }));
                }
            }
        }

        private int StartFieldParticles(SmokeField field)
        {
            if (!_particleAssetReady) return 0;
            Vector3[] positions =
            {
                field.Center,
                field.Center + new Vector3(2.7f, 0.8f, 0f),
                field.Center + new Vector3(-1.6f, -2.4f, 0f),
            };
            foreach (Vector3 position in positions)
            {
                int handle = StartLoopedColoredSmoke(position, field.Color);
                if (handle > 0) field.ParticleHandles.Add(handle);
            }
            return field.ParticleHandles.Count;
        }

        private static int StartLoopedColoredSmoke(
            Vector3 position, SmokeColor color)
        {
            Function.Call(Hash.USE_PARTICLE_FX_ASSET,
                SmokeParticleAsset);
            int handle = Function.Call<int>(
                Hash.START_PARTICLE_FX_LOOPED_AT_COORD,
                SmokeParticleEffect,
                position.X, position.Y, position.Z + 0.1f,
                0f, 0f, 0f, 1.75f,
                false, false, false, false);
            if (handle <= 0) return 0;
            Function.Call(Hash.SET_PARTICLE_FX_LOOPED_COLOUR,
                handle, color.Red, color.Green, color.Blue, false);
            Function.Call(Hash.SET_PARTICLE_FX_LOOPED_ALPHA,
                handle, 1.0f);
            return handle;
        }

        private static void StopFieldParticles(SmokeField field)
        {
            foreach (int handle in field.ParticleHandles)
                if (Function.Call<bool>(
                        Hash.DOES_PARTICLE_FX_LOOPED_EXIST, handle))
                    Function.Call(Hash.STOP_PARTICLE_FX_LOOPED,
                        handle, false);
            field.ParticleHandles.Clear();
        }

        private void EmitSupplementalPulse(
            SmokeField field, int now, int elapsedMs)
        {
            int emitterCount =
                EnhancedSmokePolicy.SupplementalEmitterCount(elapsedMs);
            float phase = field.SupplementalPulseIndex * 0.67f;
            float scale = EnhancedSmokePolicy.SupplementalScale(
                elapsedMs, field.Color?.Name);
            int emitted = 0;
            for (int index = 0; index < emitterCount; index++)
            {
                Vector3 offset;
                if (index == 0)
                {
                    offset = new Vector3(0f, 0f, 0f);
                }
                else
                {
                    float angle = phase +
                        ((float)Math.PI * 2f * (index - 1) /
                            Math.Max(1, emitterCount - 1));
                    float radius = EnhancedSmokePolicy.SupplementalRadius(
                        field.SupplementalPulseIndex + index);
                    offset = new Vector3(
                        (float)Math.Cos(angle) * radius,
                        (float)Math.Sin(angle) * radius,
                        0.08f * ((field.SupplementalPulseIndex + index) % 3));
                }
                if (StartSupplementalColoredSmoke(
                        field.Center + offset, field.Color,
                        phase * 57.29578f, scale))
                    emitted++;
            }

            field.SupplementalPulseIndex++;
            field.SupplementalEffectsEmitted += emitted;
            _supplementalPulses++;
            _supplementalEffects += emitted;
            if (!EnhancedSmokePolicy.ShouldLogSupplementalPulse(
                    field.SupplementalPulseIndex))
                return;
            PhysicsExperimentLog.Info(
                "enhanced_smoke_supplemental_pulse",
                SmokeFieldFields(field, now,
                    new Dictionary<string, object>
                    {
                        { "asset", SupplementalParticleAsset },
                        { "effect", SupplementalParticleEffect },
                        { "requested_emitters", emitterCount },
                        { "effects_emitted", emitted },
                        { "scale", scale },
                        { "interval_ms",
                            EnhancedSmokePolicy.SupplementalPulseIntervalMs(
                                elapsedMs) },
                    }));
        }

        private static bool StartSupplementalColoredSmoke(
            Vector3 position, SmokeColor color, float heading, float scale)
        {
            Function.Call(Hash.USE_PARTICLE_FX_ASSET,
                SupplementalParticleAsset);
            // Non-looped tint and alpha are next-call state. Set both before
            // each emission so concurrently active fields cannot inherit the
            // previous field's colour.
            Function.Call(Hash.SET_PARTICLE_FX_NON_LOOPED_COLOUR,
                color.Red, color.Green, color.Blue);
            Function.Call(Hash.SET_PARTICLE_FX_NON_LOOPED_ALPHA, 0.92f);
            bool started = Function.Call<bool>(
                Hash.START_PARTICLE_FX_NON_LOOPED_AT_COORD,
                SupplementalParticleEffect,
                position.X, position.Y, position.Z + 0.12f,
                0f, 0f, heading, scale,
                false, false, false);
            return started;
        }

        private void EmitFallbackPulse(SmokeField field, int now)
        {
            float phase = field.PulseIndex * 1.884f;
            Vector3 firstOffset = new Vector3(
                (float)Math.Cos(phase) * 2.8f,
                (float)Math.Sin(phase) * 2.8f, 0f);
            Vector3 secondOffset = new Vector3(
                (float)Math.Cos(phase + 2.35f) * 3.2f,
                (float)Math.Sin(phase + 2.35f) * 3.2f, 0f);
            EmitSmokeExplosion(field.Center);
            EmitSmokeExplosion(field.Center + firstOffset);
            EmitSmokeExplosion(field.Center + secondOffset);
            field.PulseIndex++;
            _pulses++;
            PhysicsExperimentLog.Info("enhanced_smoke_fallback_pulse",
                SmokeFieldFields(field, now,
                    new Dictionary<string, object>
                    {
                        { "columns", 3 },
                        { "no_damage", true },
                        { "color", field.Color.Name },
                        { "particle_effect_started", false },
                        { "native_smoke_fallback", true },
                    }));
        }

        private void EnsureParticleAssets()
        {
            if (!_particleAssetReady)
            {
                Function.Call(Hash.REQUEST_NAMED_PTFX_ASSET,
                    SmokeParticleAsset);
                _particleAssetReady = Function.Call<bool>(
                    Hash.HAS_NAMED_PTFX_ASSET_LOADED,
                    SmokeParticleAsset);
            }
            if (!_supplementalParticleAssetReady)
            {
                Function.Call(Hash.REQUEST_NAMED_PTFX_ASSET,
                    SupplementalParticleAsset);
                _supplementalParticleAssetReady = Function.Call<bool>(
                    Hash.HAS_NAMED_PTFX_ASSET_LOADED,
                    SupplementalParticleAsset);
            }
        }

        private static void EmitSmokeExplosion(Vector3 position)
        {
            Function.Call(Hash.ADD_EXPLOSION,
                position.X, position.Y, position.Z + 0.1f,
                SmokeGrenadeExplosionType, 0f,
                false, false, 0f, true);
        }

        private SmokeColor ResolveSmokeColor(
            string reason, int throwerHandle)
        {
            Ped player = Game.Player.Character;
            bool playerOwned = player != null && player.Exists() &&
                player.Handle == throwerHandle;
            return GetSmokeColor(EnhancedSmokePolicy.ColorForReason(
                reason, "white", playerOwned));
        }

        private static SmokeColor GetSmokeColor(string requested)
        {
            switch (EnhancedSmokePolicy.NormalizeColorName(requested))
            {
                case "red":
                    return new SmokeColor { Name = "red",
                        Red = 1f, Green = 0.08f, Blue = 0.05f };
                case "orange":
                    return new SmokeColor { Name = "orange",
                        Red = 1f, Green = 0.32f, Blue = 0.03f };
                case "yellow":
                    return new SmokeColor { Name = "yellow",
                        Red = 1f, Green = 0.85f, Blue = 0.05f };
                case "green":
                    return new SmokeColor { Name = "green",
                        Red = 0.08f, Green = 0.9f, Blue = 0.12f };
                case "blue":
                    return new SmokeColor { Name = "blue",
                        Red = 0.06f, Green = 0.3f, Blue = 1f };
                case "purple":
                    return new SmokeColor { Name = "purple",
                        Red = 0.55f, Green = 0.08f, Blue = 0.9f };
                default:
                    return new SmokeColor { Name = "white",
                        Red = 0.95f, Green = 0.95f, Blue = 0.95f };
            }
        }

        private void PruneConsumedProjectiles(int now)
        {
            var expired = new List<int>();
            foreach (KeyValuePair<int, int> pair in _consumedProjectiles)
                if (unchecked(now - pair.Value) >= 0)
                    expired.Add(pair.Key);
            foreach (int handle in expired)
                _consumedProjectiles.Remove(handle);
            expired.Clear();
            foreach (KeyValuePair<int, ExpectedSmoke> pair in
                _expectedSmokes)
                if (unchecked(now - pair.Value.ExpiresAt) >= 0)
                    expired.Add(pair.Key);
            foreach (int owner in expired)
            {
                ExpectedSmoke expected = _expectedSmokes[owner];
                _expectedSmokes.Remove(owner);
                PhysicsExperimentLog.Info(
                    "enhanced_smoke_throw_expectation_expired",
                    new Dictionary<string, object>
                    {
                        { "thrower", owner },
                        { "reason", expected.Reason },
                    });
            }
        }

        private static Dictionary<string, object> ProjectileFields(
            int handle, TrackedProjectile tracked, int now,
            Dictionary<string, object> fields)
        {
            fields = fields ?? new Dictionary<string, object>();
            fields["projectile"] = handle;
            fields["owner"] = tracked.OwnerHandle;
            fields["x"] = tracked.LastPosition.X;
            fields["y"] = tracked.LastPosition.Y;
            fields["z"] = tracked.LastPosition.Z;
            fields["elapsed_ms"] = unchecked(now - tracked.StartedAt);
            fields["weapon_hash"] = tracked.WeaponHash;
            fields["weapon_name"] = tracked.WeaponName ?? "unknown";
            fields["expected_reason"] = tracked.Reason ?? "none";
            return fields;
        }

        private static Dictionary<string, object> MotionFields(
            TrackedProjectile tracked,
            Dictionary<string, object> fields)
        {
            fields = fields ?? new Dictionary<string, object>();
            fields["speed"] = tracked.LastSpeed;
            fields["vertical_speed"] = tracked.LastVerticalSpeed;
            fields["scan_displacement"] = tracked.LastDisplacement;
            fields["stationary_ms"] = tracked.LastStationaryElapsedMs;
            fields["is_in_air"] = tracked.LastIsInAir;
            fields["has_collided"] = tracked.LastHasCollided;
            return fields;
        }

        private static Dictionary<string, object> SmokeFieldFields(
            SmokeField field, int now,
            Dictionary<string, object> fields)
        {
            fields = fields ?? new Dictionary<string, object>();
            fields["thrower"] = field.ThrowerHandle;
            fields["reason"] = field.Reason;
            fields["color"] = field.Color?.Name ?? "white";
            fields["pulse_index"] = field.PulseIndex;
            fields["center_x"] = field.Center.X;
            fields["center_y"] = field.Center.Y;
            fields["center_z"] = field.Center.Z;
            fields["remaining_ms"] = Math.Max(0,
                unchecked(field.ExpiresAt - now));
            fields["particle_handles"] = field.ParticleHandles.Count;
            fields["supplemental_pulse_index"] =
                field.SupplementalPulseIndex;
            fields["supplemental_effects_emitted"] =
                field.SupplementalEffectsEmitted;
            return fields;
        }

        private void WriteHeartbeat(int now)
        {
            PhysicsExperimentLog.Info("enhanced_smoke_heartbeat",
                new Dictionary<string, object>
                {
                    { "game_time_ms", now },
                    { "tracked_projectiles", _projectiles.Count },
                    { "consumed_projectiles", _consumedProjectiles.Count },
                    { "active_fields", _fields.Count },
                    { "active_particle_handles", ActiveParticleHandleCount() },
                    { "rpf_tuning_installed", _rpfTuningInstalled },
                    { "weapon_dlc_marker_present",
                        _weaponDlcMarkerPresent },
                    { "weapon_dlc_installed", _weaponDlcInstalled },
                    { "valid_custom_weapon_types",
                        _availableCustomWeaponTypes },
                    { "expected_smokes", _expectedSmokes.Count },
                    { "particle_asset_ready", _particleAssetReady },
                    { "supplemental_particle_asset_ready",
                        _supplementalParticleAssetReady },
                    { "custom_weapon_types",
                        SmokeGrenadeCatalog.Products.Length },
                    { "projectiles_tracked_total", _projectilesTracked },
                    { "projectiles_settled_total", _projectilesSettled },
                    { "projectiles_expired_unsettled_total",
                        _projectilesExpiredUnsettled },
                    { "fields_started", _fieldsStarted },
                    { "fields_deduplicated", _fieldsDeduplicated },
                    { "color_overlaps", _colorOverlaps },
                    { "fields_rejected", _fieldsRejected },
                    { "pulses", _pulses },
                    { "supplemental_pulses", _supplementalPulses },
                    { "supplemental_effects", _supplementalEffects },
                    { "fields_completed", _fieldsCompleted },
                    { "exceptions", _exceptions },
                });
        }

        private int ActiveParticleHandleCount()
        {
            int count = 0;
            foreach (SmokeField field in _fields)
                count += field.ParticleHandles.Count;
            return count;
        }
    }
}
