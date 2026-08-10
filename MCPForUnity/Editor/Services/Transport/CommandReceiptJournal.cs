using System;
using System.Collections.Generic;
using System.IO;
using Newtonsoft.Json;
using Newtonsoft.Json.Linq;
using UnityEngine;

namespace MCPForUnity.Editor.Services.Transport
{
    internal sealed class CommandReceipt
    {
        [JsonProperty("id")]
        public string Id;

        [JsonProperty("project_hash")]
        public string ProjectHash;

        [JsonProperty("name")]
        public string Name;

        [JsonProperty("envelope_sha256")]
        public string EnvelopeSha256;

        [JsonProperty("state")]
        public string State;

        [JsonProperty("result")]
        public JToken Result;
    }

    internal sealed class CommandReceiptJournal
    {
        internal const int ProtocolVersion = 1;
        internal const int MaximumReceipts = 256;
        internal static readonly TimeSpan ReceiptTtl = TimeSpan.FromHours(24);

        private readonly string _root;

        internal CommandReceiptJournal(string root)
        {
            _root = root ?? throw new ArgumentNullException(nameof(root));
        }

        internal static CommandReceiptJournal ForCurrentProject()
        {
            string assets = Application.dataPath.TrimEnd('/', '\\');
            string project = Directory.GetParent(assets)?.FullName ?? assets;
            return new CommandReceiptJournal(
                Path.Combine(project, "Library", "UnityMCP", "CommandReceipts"));
        }

        internal CommandReceipt Begin(
            string id,
            string projectHash,
            string name,
            string envelopeSha256,
            out bool created)
        {
            ValidateId(id);
            PruneExpired();
            CommandReceipt existing = Read(id);
            if (existing != null)
            {
                created = false;
                if (!string.Equals(existing.ProjectHash, projectHash, StringComparison.Ordinal)
                    || !string.Equals(existing.Name, name, StringComparison.Ordinal)
                    || !string.Equals(existing.EnvelopeSha256, envelopeSha256, StringComparison.Ordinal))
                {
                    throw new InvalidDataException("Command id is already bound to another envelope.");
                }
                return existing;
            }

            Directory.CreateDirectory(_root);
            if (Directory.GetFiles(_root, "*.json").Length >= MaximumReceipts)
            {
                throw new InvalidOperationException("Command receipt capacity is exhausted.");
            }

            var receipt = new CommandReceipt
            {
                Id = id,
                ProjectHash = projectHash,
                Name = name,
                EnvelopeSha256 = envelopeSha256,
                State = "started"
            };
            Write(receipt);
            created = true;
            return receipt;
        }

        internal void Complete(CommandReceipt receipt, JToken result)
        {
            if (receipt == null) throw new ArgumentNullException(nameof(receipt));
            receipt.State = "completed";
            receipt.Result = result?.DeepClone() ?? new JObject();
            Write(receipt);
        }

        internal IReadOnlyList<CommandReceipt> All()
        {
            if (!Directory.Exists(_root)) return Array.Empty<CommandReceipt>();
            PruneExpired();
            var receipts = new List<CommandReceipt>();
            foreach (string path in Directory.GetFiles(_root, "*.json"))
            {
                receipts.Add(ReadPath(path));
            }
            return receipts;
        }

        internal void Acknowledge(string id)
        {
            ValidateId(id);
            string path = PathFor(id);
            if (File.Exists(path)) File.Delete(path);
        }

        private CommandReceipt Read(string id)
        {
            string path = PathFor(id);
            return File.Exists(path) ? ReadPath(path) : null;
        }

        private void PruneExpired()
        {
            if (!Directory.Exists(_root)) return;
            DateTime cutoff = DateTime.UtcNow - ReceiptTtl;
            foreach (string path in Directory.GetFiles(_root, "*.json"))
            {
                if (File.GetLastWriteTimeUtc(path) < cutoff)
                {
                    File.Delete(path);
                }
            }
        }

        private static CommandReceipt ReadPath(string path)
        {
            try
            {
                var receipt = JsonConvert.DeserializeObject<CommandReceipt>(File.ReadAllText(path));
                if (receipt == null
                    || string.IsNullOrEmpty(receipt.Id)
                    || string.IsNullOrEmpty(receipt.ProjectHash)
                    || string.IsNullOrEmpty(receipt.Name)
                    || string.IsNullOrEmpty(receipt.EnvelopeSha256)
                    || (receipt.State != "started" && receipt.State != "completed")
                    || (receipt.State == "completed" && receipt.Result == null))
                {
                    throw new InvalidDataException("Command receipt is incomplete.");
                }
                return receipt;
            }
            catch (JsonException ex)
            {
                throw new InvalidDataException("Command receipt JSON is invalid.", ex);
            }
        }

        private void Write(CommandReceipt receipt)
        {
            Directory.CreateDirectory(_root);
            string path = PathFor(receipt.Id);
            string temporary = path + "." + Guid.NewGuid().ToString("N") + ".tmp";
            File.WriteAllText(temporary, JsonConvert.SerializeObject(receipt));
            try
            {
                if (File.Exists(path))
                {
                    File.Replace(temporary, path, null);
                }
                else
                {
                    File.Move(temporary, path);
                }
            }
            finally
            {
                if (File.Exists(temporary)) File.Delete(temporary);
            }
        }

        private string PathFor(string id)
        {
            ValidateId(id);
            return Path.Combine(_root, id + ".json");
        }

        private static void ValidateId(string id)
        {
            if (string.IsNullOrEmpty(id) || id.Length > 128)
            {
                throw new ArgumentException("Command receipt id is invalid.", nameof(id));
            }
            foreach (char character in id)
            {
                bool valid = character >= 'a' && character <= 'z'
                    || character >= 'A' && character <= 'Z'
                    || character >= '0' && character <= '9'
                    || character == '-'
                    || character == '_';
                if (!valid)
                {
                    throw new ArgumentException("Command receipt id is invalid.", nameof(id));
                }
            }
        }
    }
}
