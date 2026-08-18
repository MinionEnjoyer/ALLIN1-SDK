// GBAY catalogue and policy for ALLIN1's purchasable smoke colours.

using System;
using System.Collections.Generic;
using System.Drawing;
using System.Runtime.InteropServices;
using GTA;
using GTA.Native;

namespace ALLIN1
{
    internal sealed class SmokeGrenadeProduct
    {
        internal string Id;
        internal string ColorName;
        internal string DisplayName;
        internal string WeaponName;
        internal string AmmoName;
        internal string HumanNameLabel;
        internal int UnitPrice;
        internal int BundleQuantity;
        internal Color UiColor;
    }

    internal static class SmokeGrenadeCatalog
    {
        // GET_DLC_WEAPON_DATA writes GTA's packed DLC-weapon record. We only
        // need the weapon hash at byte offset 8, so avoid binding the rest of
        // the undocumented structure to a game-build-specific C# layout.
        private const int DlcWeaponDataSize = 312;
        private const int DlcWeaponHashOffset = 8;
        private const int MaximumReasonableDlcWeapons = 512;
        internal const string NativeWeaponName = "WEAPON_SMOKEGRENADE";
        internal const int DefaultUnitPrice = 150;
        internal const int DefaultBundleQuantity = 5;
        internal const int MaximumPerColor = 5;

        internal static readonly SmokeGrenadeProduct[] Products =
        {
            Product("ALLIN1_SMOKE_WHITE", "white", "White Smoke Grenades",
                236, 239, 235),
            Product("ALLIN1_SMOKE_RED", "red", "Red Smoke Grenades",
                230, 55, 48),
            Product("ALLIN1_SMOKE_ORANGE", "orange", "Orange Smoke Grenades",
                242, 126, 32),
            Product("ALLIN1_SMOKE_YELLOW", "yellow", "Yellow Smoke Grenades",
                239, 205, 55),
            Product("ALLIN1_SMOKE_GREEN", "green", "Green Smoke Grenades",
                55, 190, 78),
            Product("ALLIN1_SMOKE_BLUE", "blue", "Blue Smoke Grenades",
                52, 108, 225),
            Product("ALLIN1_SMOKE_PURPLE", "purple", "Purple Smoke Grenades",
                145, 68, 205),
        };

        internal static readonly string[] ProductIds = BuildProductIds();

        private static SmokeGrenadeProduct Product(
            string id, string color, string displayName,
            int red, int green, int blue)
        {
            string suffix = color.ToUpperInvariant();
            return new SmokeGrenadeProduct
            {
                Id = id,
                ColorName = color,
                DisplayName = displayName,
                WeaponName = "WEAPON_ALLIN1_SMOKE_" + suffix,
                AmmoName = "AMMO_ALLIN1_SMOKE_" + suffix,
                HumanNameLabel = "WT_A1SM" +
                    suffix.Substring(0, Math.Min(3, suffix.Length)),
                UnitPrice = DefaultUnitPrice,
                BundleQuantity = DefaultBundleQuantity,
                UiColor = Color.FromArgb(255, red, green, blue),
            };
        }

        private static string[] BuildProductIds()
        {
            var result = new string[Products.Length];
            for (int index = 0; index < Products.Length; index++)
                result[index] = Products[index].Id;
            return result;
        }

        internal static bool IsProduct(string id)
        {
            return TryGetProduct(id, out SmokeGrenadeProduct _);
        }

        internal static bool TryGetProduct(
            string id, out SmokeGrenadeProduct product)
        {
            foreach (SmokeGrenadeProduct candidate in Products)
            {
                if (!string.Equals(candidate.Id, id,
                        StringComparison.OrdinalIgnoreCase)) continue;
                product = candidate;
                return true;
            }
            product = null;
            return false;
        }

        internal static bool IsSupportedColor(string color)
        {
            return TryGetByColor(color, out SmokeGrenadeProduct _);
        }

        internal static bool TryGetByWeaponHash(
            int weaponHash, out SmokeGrenadeProduct product)
        {
            foreach (SmokeGrenadeProduct candidate in Products)
            {
                if (Game.GenerateHash(candidate.WeaponName) != weaponHash)
                    continue;
                product = candidate;
                return true;
            }
            product = null;
            return false;
        }

        internal static int AvailableCustomWeaponCount()
        {
            int available = 0;
            foreach (SmokeGrenadeProduct product in Products)
            {
                int weaponHash = Game.GenerateHash(product.WeaponName);
                if (Function.Call<bool>(Hash.IS_WEAPON_VALID, weaponHash))
                    available++;
            }
            return available;
        }

        internal static bool AreAllCustomWeaponsAvailable()
        {
            return AvailableCustomWeaponCount() == Products.Length;
        }

        internal static int RegisteredCustomWeaponCount(
            out int totalDlcWeapons, out string failure)
        {
            totalDlcWeapons = 0;
            failure = "";
            IntPtr record = IntPtr.Zero;
            try
            {
                totalDlcWeapons = Function.Call<int>(
                    Hash.GET_NUM_DLC_WEAPONS);
                if (totalDlcWeapons < 0 ||
                    totalDlcWeapons > MaximumReasonableDlcWeapons)
                {
                    failure = "invalid_dlc_weapon_count:" + totalDlcWeapons;
                    return 0;
                }

                var wanted = new HashSet<int>();
                foreach (SmokeGrenadeProduct product in Products)
                    wanted.Add(Game.GenerateHash(product.WeaponName));
                var found = new HashSet<int>();
                byte[] empty = new byte[DlcWeaponDataSize];
                record = Marshal.AllocHGlobal(DlcWeaponDataSize);
                for (int index = 0; index < totalDlcWeapons; index++)
                {
                    Marshal.Copy(empty, 0, record, empty.Length);
                    if (!Function.Call<bool>(Hash.GET_DLC_WEAPON_DATA,
                            index, record))
                        continue;
                    int weaponHash = Marshal.ReadInt32(
                        record, DlcWeaponHashOffset);
                    if (wanted.Contains(weaponHash)) found.Add(weaponHash);
                }
                return found.Count;
            }
            catch (Exception ex)
            {
                failure = ex.GetType().Name + ":" + ex.Message;
                return 0;
            }
            finally
            {
                if (record != IntPtr.Zero) Marshal.FreeHGlobal(record);
            }
        }

        internal static bool TryGetByColor(
            string color, out SmokeGrenadeProduct product)
        {
            foreach (SmokeGrenadeProduct candidate in Products)
            {
                if (!string.Equals(candidate.ColorName, color,
                        StringComparison.OrdinalIgnoreCase)) continue;
                product = candidate;
                return true;
            }
            product = null;
            return false;
        }

        internal static string NormalizeColor(string color)
        {
            return TryGetByColor((color ?? "").Trim(),
                out SmokeGrenadeProduct product)
                ? product.ColorName : "white";
        }

        internal static string DisplayNameForColor(string color)
        {
            return TryGetByColor(NormalizeColor(color),
                out SmokeGrenadeProduct product)
                ? product.DisplayName : "White Smoke Grenades";
        }
    }
}
