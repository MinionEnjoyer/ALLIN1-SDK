// SmokeRpfIntegration.cs -- self-contained map of the archive work used by
// the Colored Smoke Grenades SDK example.
//
// This file intentionally describes the integration contract rather than
// shipping the ALLIN1 launcher's private archive-writing implementation. An
// installer should execute these stages through the SDK's verified RPF plan
// and transaction APIs, never by replacing an entire current-game archive.

using System;
using System.Collections.Generic;

namespace ALLIN1.SdkExamples
{
    internal sealed class SmokeArchiveEdit
    {
        internal string Archive;
        internal string Entry;
        internal string Strategy;
        internal int ExpectedAdditions;
    }

    internal static class SmokeRpfIntegration
    {
        internal static readonly string[] Colors =
        {
            "WHITE", "RED", "ORANGE", "YELLOW", "GREEN", "BLUE", "PURPLE",
        };

        internal static readonly SmokeArchiveEdit[] Edits =
        {
            new SmokeArchiveEdit
            {
                Archive = "mods/update/update.rpf",
                Entry = "common/data/ai/weapons.meta",
                Strategy = "Clone the installed WEAPON_SMOKEGRENADE weapon and ammo records; change only the custom identifiers and smoke fields.",
                ExpectedAdditions = 14,
            },
            new SmokeArchiveEdit
            {
                Archive = "mods/update/update.rpf",
                Entry = "common/data/ai/weaponanimations.meta",
                Strategy = "Clone the installed smoke mapping in all six compatible animation sets.",
                ExpectedAdditions = 42,
            },
            new SmokeArchiveEdit
            {
                Archive = "mods/update/update.rpf/x64/patch/data/lang/american_rel.rpf",
                Entry = "global.gxt2",
                Strategy = "Append one native weapon-wheel label for each custom weapon hash.",
                ExpectedAdditions = 7,
            },
            new SmokeArchiveEdit
            {
                Archive = "mods/update/update.rpf/x64/data/cdimages/scaleform_generic.rpf",
                Entry = "hud.gfx",
                Strategy = "Alias each custom signed weapon hash to the existing BZ Gas artwork frame.",
                ExpectedAdditions = 7,
            },
        };

        internal static IEnumerable<string> RequiredWeaponNames()
        {
            foreach (string color in Colors)
                yield return "WEAPON_ALLIN1_SMOKE_" + color;
        }

        internal static IEnumerable<string> RequiredAmmoNames()
        {
            foreach (string color in Colors)
                yield return "AMMO_ALLIN1_SMOKE_" + color;
        }

        internal static void ValidatePlan(
            bool gtaIsRunning,
            bool exactBackupsPrepared,
            bool everySourceComesFromCurrentBuild,
            bool roundTripVerified)
        {
            if (gtaIsRunning)
                throw new InvalidOperationException("Close GTA V before archive writes.");
            if (!exactBackupsPrepared)
                throw new InvalidOperationException("Exact entry backups are required.");
            if (!everySourceComesFromCurrentBuild)
                throw new InvalidOperationException("Do not merge stale full-file replacements.");
            if (!roundTripVerified)
                throw new InvalidOperationException("Removing the additions must reproduce the original bytes.");
        }

        internal static void CommitOrRollback(
            Action applyVerifiedPlan,
            Func<bool> verifyInstalledEntries,
            Action restoreExactBackups)
        {
            try
            {
                applyVerifiedPlan();
                if (!verifyInstalledEntries())
                    throw new InvalidOperationException("Post-write verification failed.");
            }
            catch
            {
                restoreExactBackups();
                throw;
            }
        }
    }
}
